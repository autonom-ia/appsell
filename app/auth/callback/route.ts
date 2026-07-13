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
import {
  DomainApiError,
  isDomainApiConfigured,
  readDomainIdentity,
  syncDomainIdentity
} from "@/lib/internal-api-client.server";
import { ONBOARD_SELLER_CSRF_ACTION } from "@/lib/mutation-protection.server";
import { demoProfileForSubject } from "@/lib/demo-config";

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
  let issuedSessionId: string | undefined;
  let demoSuperadmin = false;
  try {
    const sessionId = await oauthService().callback({
      transactionId: request.cookies.get(transactionCookieName())?.value,
      code: request.nextUrl.searchParams.get("code") ?? undefined,
      state: request.nextUrl.searchParams.get("state") ?? undefined
    });
    issuedSessionId = sessionId;
    const session = await oauthService().getSession(sessionId);
    if (!session) throw new DomainApiError(503, "session_unavailable_after_callback");
    const demoProfile = demoProfileForSubject(session.subject);
    demoSuperadmin = demoProfile?.persona === "superadmin";
    let platformSuperadmin = false;
    if (demoSuperadmin && !isDomainApiConfigured()) {
      await oauthService().logout(sessionId);
      throw new DomainApiError(503, "domain_api_not_configured");
    }
    if (isDomainApiConfigured()) {
      try {
        await syncDomainIdentity(sessionId, session);
        if (!demoProfile || demoProfile.persona === "superadmin") {
          platformSuperadmin = hasPlatformSuperadminGrant(
            await readDomainIdentity(sessionId, session)
          );
          if (demoProfile?.persona === "superadmin" && !platformSuperadmin) {
            throw new DomainApiError(403, "demo_superadmin_grant_required");
          }
        }
      } catch (error) {
        await oauthService().logout(sessionId);
        throw error;
      }
    }
    const response = NextResponse.redirect(new URL(
      platformSuperadmin ? "/platform/demo-inspection" : demoProfile?.home ?? "/dashboard",
      configuredPublicOrigin()
    ));
    expireTransactionCookie(response);
    response.cookies.set(sessionCookieName(), sessionId, sessionCookieOptions());
    response.cookies.set(csrfCookieName(), csrfTokenForSession(sessionId, LOGOUT_CSRF_ACTION), csrfCookieOptions());
    if (demoProfile?.persona === "buyer" || demoSuperadmin) {
      expireSellerCookies(response);
    } else {
      response.cookies.set(sellerCsrfCookieName(), csrfTokenForSession(sessionId, ONBOARD_SELLER_CSRF_ACTION), csrfCookieOptions());
      response.cookies.set(productCsrfCookieName(), csrfTokenForSession(sessionId, CREATE_PRODUCT_DRAFT_CSRF_ACTION), csrfCookieOptions());
      response.cookies.set(productUpdateCsrfCookieName(), csrfTokenForSession(sessionId, UPDATE_PRODUCT_DRAFT_CSRF_ACTION), csrfCookieOptions());
      response.cookies.set(productPublishCsrfCookieName(), csrfTokenForSession(sessionId, PUBLISH_PRODUCT_DRAFT_CSRF_ACTION), csrfCookieOptions());
    }
    response.headers.set("cache-control", "no-store");
    return response;
  } catch (error) {
    const response = NextResponse.json({
      error: isOAuthStoreUnavailable(error) ? "oauth_store_unavailable" :
        error instanceof DomainApiError ? error.code : "invalid_oauth_callback"
    }, {
      status: isOAuthStoreUnavailable(error) || error instanceof DomainApiError ? 503 : 400,
      headers: { "cache-control": "no-store" }
    });
    if (issuedSessionId) {
      expireTransactionCookie(response);
      response.cookies.set(sessionCookieName(), "", { ...sessionCookieOptions(), maxAge: 0 });
      if (demoSuperadmin) expireSellerCookies(response);
    }
    return response;
  }
}

function hasPlatformSuperadminGrant(value: unknown) {
  return typeof value === "object" && value !== null && !Array.isArray(value) &&
    Array.isArray((value as Record<string, unknown>).platform_roles) &&
    ((value as Record<string, unknown>).platform_roles as unknown[]).includes("platform_superadmin");
}

function expireSellerCookies(response: NextResponse) {
  for (const name of [
    sellerCsrfCookieName(),
    productCsrfCookieName(),
    productUpdateCsrfCookieName(),
    productPublishCsrfCookieName()
  ]) {
    response.cookies.set(name, "", { ...csrfCookieOptions(), maxAge: 0 });
  }
}

function expireTransactionCookie(response: NextResponse) {
  response.cookies.set(transactionCookieName(), "", {
    ...transactionCookieOptions(),
    maxAge: 0
  });
}
