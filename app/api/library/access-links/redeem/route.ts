import { NextRequest, NextResponse } from "next/server";

import { accessLinkDigest, isAccessLinkToken } from "@/lib/access-link-token.server";
import { sessionCookieName } from "@/lib/cookies";
import {
  DomainApiError,
  redeemDomainAccessLink
} from "@/lib/internal-api-client.server";
import { PayloadTooLargeError, readLimitedForm } from "@/lib/limited-form.server";
import {
  REDEEM_ACCESS_LINK_CSRF_ACTION,
  verifyMutation
} from "@/lib/mutation-protection.server";
import { oauthService } from "@/lib/oauth-service.server";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";

const FIELDS = ["_csrf", "idempotency_key", "token"] as const;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
// A token is a 256 bit secret, so guessing is already hopeless. The ceiling
// exists so nobody can turn this endpoint into a fast oracle, and so a single
// account cannot hammer it.
const REDEEM_RATE_LIMIT = { maxPerClient: 20, maxGlobal: 600, windowMs: 60_000 };

export async function POST(request: NextRequest) {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId || !hasConfiguredOrigin(request, configuredPublicOrigin())) {
    return errorResponse("forbidden_mutation", 403);
  }
  try {
    const service = oauthService();
    const session = await service.getSession(sessionId);
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
    if (!verifyMutation(request, sessionId, REDEEM_ACCESS_LINK_CSRF_ACTION, value(form, "_csrf"))) {
      return errorResponse("forbidden_mutation", 403);
    }

    const idempotencyKey = value(form, "idempotency_key");
    const token = value(form, "token");
    if (!UUID.test(idempotencyKey) || !isAccessLinkToken(token)) {
      return errorResponse("invalid_request_body", 422);
    }

    const allowed = await service.consumeRateLimit(
      "access-link-redeem",
      sessionId,
      REDEEM_RATE_LIMIT
    );
    if (!allowed) return errorResponse("rate_limited", 429);

    // Only the digest leaves this process. The raw token is never logged and
    // never forwarded, so a request trace cannot be replayed into access.
    const payload = await redeemDomainAccessLink(sessionId, session, {
      idempotencyKey,
      tokenSha256: accessLinkDigest(token)
    });
    const created = typeof payload === "object" && payload !== null &&
      "created" in payload && payload.created === true;
    return NextResponse.json(payload, { status: created ? 201 : 200, headers: noStore() });
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
  return errorResponse("access_link_unavailable", 503);
}
