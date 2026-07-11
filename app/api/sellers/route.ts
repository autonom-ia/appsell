import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import {
  DomainApiError,
  listDomainSellers,
  onboardDomainSeller
} from "@/lib/internal-api-client.server";
import {
  ONBOARD_SELLER_CSRF_ACTION,
  verifyMutation
} from "@/lib/mutation-protection.server";
import { oauthService } from "@/lib/oauth-service.server";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export async function GET(request: NextRequest) {
  try {
    const authenticated = await authenticatedSession(request);
    if (!authenticated) return unauthorized();
    const payload = await listDomainSellers(authenticated.sessionId, authenticated.session);
    return NextResponse.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}

export async function POST(request: NextRequest) {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId || !hasConfiguredOrigin(request, configuredPublicOrigin())) {
    return forbiddenMutation();
  }
  let authenticated: Awaited<ReturnType<typeof authenticatedSession>>;
  try {
    authenticated = await authenticatedSession(request);
  } catch (error) {
    return mappedError(error);
  }
  if (!authenticated) return unauthorized();
  let form: FormData | null;
  try {
    form = await readLimitedForm(request);
  } catch (error) {
    if (error instanceof PayloadTooLargeError) {
      return NextResponse.json({ error: "payload_too_large" }, {
        status: 413,
        headers: { "cache-control": "no-store" }
      });
    }
    form = null;
  }
  const csrf = form?.get("_csrf");
  if (!verifyMutation(
    request,
    sessionId,
    ONBOARD_SELLER_CSRF_ACTION,
    typeof csrf === "string" ? csrf : null
  )) {
    return forbiddenMutation();
  }
  const idempotencyKey = form?.get("idempotency_key");
  const slug = form?.get("slug");
  const displayName = form?.get("display_name");
  if (typeof idempotencyKey !== "string" || !UUID.test(idempotencyKey)) {
    return invalidInput("form");
  }
  if (typeof slug !== "string" || slug.length < 3 || slug.length > 63 || !SLUG.test(slug)) {
    return invalidInput("slug");
  }
  if (typeof displayName !== "string" || displayName !== displayName.trim() ||
      displayName.length < 2 || displayName.length > 120) {
    return invalidInput("display_name");
  }
  try {
    const payload = await onboardDomainSeller(authenticated.sessionId, authenticated.session, {
      idempotencyKey,
      slug,
      displayName
    });
    return NextResponse.json(payload, {
      status: 201,
      headers: { "cache-control": "no-store" }
    });
  } catch (error) {
    return mappedError(error);
  }
}

class PayloadTooLargeError extends Error {}

async function readLimitedForm(request: NextRequest) {
  const contentType = request.headers.get("content-type") ?? "";
  const mediaType = contentType.split(";", 1)[0]?.trim().toLowerCase();
  if (mediaType !== "multipart/form-data" &&
      mediaType !== "application/x-www-form-urlencoded") return null;
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(declared) && declared > 16_384) throw new PayloadTooLargeError();
  if (!request.body) return null;
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > 16_384) {
      await reader.cancel();
      throw new PayloadTooLargeError();
    }
    chunks.push(value);
  }
  const body = Buffer.concat(chunks.map((chunk) => Buffer.from(chunk)));
  return new Response(body, { headers: { "content-type": contentType } }).formData();
}

async function authenticatedSession(request: NextRequest) {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId) return null;
  const session = await oauthService().getSession(sessionId);
  return session ? { sessionId, session } : null;
}

function unauthorized() {
  return NextResponse.json({ error: "unauthorized" }, {
    status: 401,
    headers: { "cache-control": "no-store" }
  });
}

function invalidInput(field: "form" | "slug" | "display_name") {
  return NextResponse.json({ error: "invalid_seller_input", field }, {
    status: 422,
    headers: { "cache-control": "no-store" }
  });
}

function forbiddenMutation() {
  return NextResponse.json({ error: "forbidden_mutation" }, {
    status: 403,
    headers: { "cache-control": "no-store" }
  });
}

function mappedError(error: unknown) {
  if (isOAuthStoreUnavailable(error)) {
    return NextResponse.json({ error: "oauth_store_unavailable" }, {
      status: 503,
      headers: { "cache-control": "no-store" }
    });
  }
  if (error instanceof DomainApiError) {
    return NextResponse.json({ error: error.code }, {
      status: error.status,
      headers: { "cache-control": "no-store" }
    });
  }
  return NextResponse.json({ error: "seller_api_unavailable" }, {
    status: 503,
    headers: { "cache-control": "no-store" }
  });
}
