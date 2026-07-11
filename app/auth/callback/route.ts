import { NextRequest, NextResponse } from "next/server";
import { oauthService } from "@/lib/oauth-service.server";
import {
  csrfCookieName,
  csrfCookieOptions,
  productCsrfCookieName,
  productPublishCsrfCookieName,
  productUpdateCsrfCookieName,
  sellerCsrfCookieName,
  sessionCookieName,
  sessionCookieOptions,
  transactionCookieName,
  transactionCookieOptions
} from "@/lib/cookies";
import {
  CREATE_PRODUCT_DRAFT_CSRF_ACTION,
  csrfTokenForSession,
  LOGOUT_CSRF_ACTION,
  PUBLISH_PRODUCT_DRAFT_CSRF_ACTION,
  UPDATE_PRODUCT_DRAFT_CSRF_ACTION
} from "@/lib/mutation-protection.server";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";
import { configuredPublicOrigin } from "@/lib/public-origin";
import { DomainApiError, isDomainApiConfigured, syncDomainIdentity } from "@/lib/internal-api-client.server";
import { ONBOARD_SELLER_CSRF_ACTION } from "@/lib/mutation-protection.server";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  if (process.env.NODE_ENV === "production") {
    const response = NextResponse.json({ error: "identity_not_configured" }, {
      status: 503,
      headers: { "cache-control": "no-store" }
    });
    expireTransactionCookie(response);
    return response;
  }
  try {
    const sessionId = await oauthService().callback({
      transactionId: request.cookies.get(transactionCookieName())?.value,
      code: request.nextUrl.searchParams.get("code") ?? undefined,
      state: request.nextUrl.searchParams.get("state") ?? undefined
    });
    if (isDomainApiConfigured()) {
      const session = await oauthService().getSession(sessionId);
      if (!session) throw new DomainApiError(503, "session_unavailable_after_callback");
      try {
        await syncDomainIdentity(sessionId, session);
      } catch (error) {
        await oauthService().logout(sessionId);
        throw error;
      }
    }
    const response = NextResponse.redirect(new URL("/dashboard", configuredPublicOrigin()));
    expireTransactionCookie(response);
    response.cookies.set(sessionCookieName(), sessionId, sessionCookieOptions());
    response.cookies.set(csrfCookieName(), csrfTokenForSession(sessionId, LOGOUT_CSRF_ACTION), csrfCookieOptions());
    response.cookies.set(sellerCsrfCookieName(), csrfTokenForSession(sessionId, ONBOARD_SELLER_CSRF_ACTION), csrfCookieOptions());
    response.cookies.set(productCsrfCookieName(), csrfTokenForSession(sessionId, CREATE_PRODUCT_DRAFT_CSRF_ACTION), csrfCookieOptions());
    response.cookies.set(productUpdateCsrfCookieName(), csrfTokenForSession(sessionId, UPDATE_PRODUCT_DRAFT_CSRF_ACTION), csrfCookieOptions());
    response.cookies.set(productPublishCsrfCookieName(), csrfTokenForSession(sessionId, PUBLISH_PRODUCT_DRAFT_CSRF_ACTION), csrfCookieOptions());
    response.headers.set("cache-control", "no-store");
    return response;
  } catch (error) {
    return NextResponse.json({
      error: isOAuthStoreUnavailable(error) ? "oauth_store_unavailable" :
        error instanceof DomainApiError ? error.code : "invalid_oauth_callback"
    }, {
      status: isOAuthStoreUnavailable(error) || error instanceof DomainApiError ? 503 : 400,
      headers: { "cache-control": "no-store" }
    });
  }
}

function expireTransactionCookie(response: NextResponse) {
  response.cookies.set(transactionCookieName(), "", {
    ...transactionCookieOptions(),
    maxAge: 0
  });
}
