import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import {
  DomainApiError,
  grantDomainPublicProductAccess
} from "@/lib/internal-api-client.server";
import { PayloadTooLargeError, readLimitedForm } from "@/lib/limited-form.server";
import {
  ENROLL_PUBLIC_PRODUCT_CSRF_ACTION,
  verifyMutation
} from "@/lib/mutation-protection.server";
import { oauthService } from "@/lib/oauth-service.server";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";

const FIELDS = ["_csrf", "idempotency_key", "seller_id", "product_id"] as const;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export async function POST(request: NextRequest) {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId || !hasConfiguredOrigin(request, configuredPublicOrigin())) {
    return errorResponse("forbidden_mutation", 403);
  }
  try {
    const session = await oauthService().getSession(sessionId);
    if (!session) return errorResponse("unauthorized", 401);
    let form: FormData | null;
    try {
      form = await readLimitedForm(request, 4_096);
    } catch (error) {
      return error instanceof PayloadTooLargeError
        ? errorResponse("payload_too_large", 413)
        : errorResponse("invalid_request_body", 422);
    }
    if (!form || !hasExactFields(form)) return errorResponse("invalid_request_body", 422);
    const csrf = value(form, "_csrf");
    if (!verifyMutation(request, sessionId, ENROLL_PUBLIC_PRODUCT_CSRF_ACTION, csrf)) {
      return errorResponse("forbidden_mutation", 403);
    }
    const idempotencyKey = value(form, "idempotency_key");
    const sellerId = value(form, "seller_id");
    const productId = value(form, "product_id");
    if (!UUID.test(idempotencyKey) || !UUID.test(sellerId) || !UUID.test(productId)) {
      return errorResponse("invalid_request_body", 422);
    }
    const payload = await grantDomainPublicProductAccess(sessionId, session, {
      sellerOrganizationId: sellerId,
      productId,
      idempotencyKey
    });
    const created = typeof payload === "object" && payload !== null &&
      "created" in payload && payload.created === true;
    return NextResponse.json(payload, {
      status: created ? 201 : 200,
      headers: noStore()
    });
  } catch (error) {
    return mappedError(error);
  }
}

function hasExactFields(form: FormData) {
  const keys = [...form.keys()];
  return keys.length === FIELDS.length &&
    new Set(keys).size === FIELDS.length &&
    keys.every((key) => FIELDS.includes(key as typeof FIELDS[number]));
}

function value(form: FormData, key: string) {
  const entry = form.get(key);
  return typeof entry === "string" ? entry : "";
}

function noStore() {
  return {
    "cache-control": "private, no-store, max-age=0",
    pragma: "no-cache",
    "x-content-type-options": "nosniff"
  };
}

function errorResponse(error: string, status: number) {
  return NextResponse.json({ error }, { status, headers: noStore() });
}

function mappedError(error: unknown) {
  if (isOAuthStoreUnavailable(error)) return errorResponse("oauth_store_unavailable", 503);
  if (error instanceof DomainApiError) return errorResponse(error.code, error.status);
  return errorResponse("public_access_unavailable", 503);
}
