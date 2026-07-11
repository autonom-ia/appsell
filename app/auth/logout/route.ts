import { NextRequest, NextResponse } from "next/server";
import { LOGOUT_CSRF_ACTION, verifyMutation } from "@/lib/mutation-protection.server";
import { oauthService } from "@/lib/oauth-service.server";
import { csrfCookieName, csrfCookieOptions, sellerCsrfCookieName, sessionCookieName, sessionCookieOptions } from "@/lib/cookies";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";
import { configuredPublicOrigin } from "@/lib/public-origin";

export async function POST(request: NextRequest) {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  const form = await request.formData().catch(() => null);
  const csrf = form?.get("_csrf");
  if (!verifyMutation(request, sessionId, LOGOUT_CSRF_ACTION, typeof csrf === "string" ? csrf : null)) {
    return NextResponse.json({ error: "forbidden_origin" }, {
      status: 403,
      headers: { "cache-control": "no-store" }
    });
  }
  try {
    await oauthService().logout(sessionId);
  } catch (error) {
    if (!isOAuthStoreUnavailable(error)) throw error;
    return NextResponse.json({ error: "oauth_store_unavailable" }, {
      status: 503,
      headers: { "cache-control": "no-store" }
    });
  }
  const response = NextResponse.redirect(new URL("/", configuredPublicOrigin()), 303);
  expireAuthCookies(response);
  response.headers.set("cache-control", "no-store");
  return response;
}

function expireAuthCookies(response: NextResponse) {
  response.cookies.set(sessionCookieName(), "", { ...sessionCookieOptions(), maxAge: 0 });
  response.cookies.set(csrfCookieName(), "", { ...csrfCookieOptions(), maxAge: 0 });
  response.cookies.set(sellerCsrfCookieName(), "", { ...csrfCookieOptions(), maxAge: 0 });
}
