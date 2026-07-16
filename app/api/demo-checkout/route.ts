import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import { parseDemoCheckoutEnvelope } from "@/lib/demo-checkout";
import {
  completeDomainDemoCheckout,
  DomainApiError,
  readDomainDemoCheckout
} from "@/lib/internal-api-client.server";
import {
  COMPLETE_DEMO_CHECKOUT_CSRF_ACTION,
  csrfTokenForSession,
  verifyMutation
} from "@/lib/mutation-protection.server";
import { oauthService } from "@/lib/oauth-service.server";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";

const FIELDS = ["_csrf", "idempotency_key", "coupon_code"] as const;

export async function GET(request: NextRequest) {
  try {
    const authenticated = await session(request);
    if (!authenticated) return unauthorized();
    const parsed = parseDemoCheckoutEnvelope(
      await readDomainDemoCheckout(authenticated.sessionId, authenticated.value)
    );
    if (!parsed || !("offer" in parsed)) return unavailable("invalid_demo_checkout_response");
    return NextResponse.json({
      checkout: parsed,
      coupon_hint: "AUTONOMIA100",
      csrf: { complete: csrfTokenForSession(authenticated.sessionId, COMPLETE_DEMO_CHECKOUT_CSRF_ACTION) }
    }, { headers: noStore() });
  } catch (error) {
    return checkoutError(error);
  }
}

export async function POST(request: NextRequest) {
  const authenticated = await session(request);
  if (!authenticated) return unauthorized();
  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return invalid();
  }
  const keys = [...form.keys()];
  if (keys.length !== FIELDS.length ||
      !keys.every((key) => FIELDS.includes(key as typeof FIELDS[number])) ||
      new Set(keys).size !== FIELDS.length) return invalid();
  const csrf = field(form, "_csrf");
  const idempotencyKey = field(form, "idempotency_key");
  const couponCode = field(form, "coupon_code");
  if (!verifyMutation(request, authenticated.sessionId, COMPLETE_DEMO_CHECKOUT_CSRF_ACTION, csrf)) {
    return NextResponse.json({ error: "invalid_csrf" }, { status: 403, headers: noStore() });
  }
  if (!isUuid(idempotencyKey) || couponCode !== couponCode.trim().toUpperCase() ||
      !/^[A-Z0-9_-]{3,32}$/.test(couponCode)) return invalid();
  try {
    const parsed = parseDemoCheckoutEnvelope(await completeDomainDemoCheckout(
      authenticated.sessionId,
      authenticated.value,
      { idempotencyKey, couponCode }
    ));
    if (!parsed || !("status" in parsed)) return unavailable("invalid_demo_checkout_response");
    return NextResponse.json({ checkout: parsed }, { status: 202, headers: noStore() });
  } catch (error) {
    return checkoutError(error);
  }
}

async function session(request: NextRequest) {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId) return null;
  const value = await oauthService().getSession(sessionId);
  return value ? { sessionId, value } : null;
}

function field(form: FormData, name: string) {
  const value = form.get(name);
  return typeof value === "string" ? value : "";
}
function isUuid(value: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);
}
function noStore() { return { "cache-control": "private, no-store" }; }
function unauthorized() { return NextResponse.json({ error: "unauthorized" }, { status: 401, headers: noStore() }); }
function invalid() { return NextResponse.json({ error: "invalid_demo_checkout_request" }, { status: 422, headers: noStore() }); }
function unavailable(code: string) { return NextResponse.json({ error: code }, { status: 503, headers: noStore() }); }
function checkoutError(error: unknown) {
  const status = error instanceof DomainApiError ? error.status : 503;
  const code = error instanceof DomainApiError ? error.code :
    isOAuthStoreUnavailable(error) ? "oauth_store_unavailable" : "demo_checkout_unavailable";
  return NextResponse.json({ error: code }, { status, headers: noStore() });
}
