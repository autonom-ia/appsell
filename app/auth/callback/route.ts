import { NextRequest, NextResponse } from "next/server";
import {
  IdentityOAuthAmbiguousError,
  IdentityOAuthConfigurationError,
  IdentityOAuthUnavailableError
} from "@/lib/identity-oauth-client.server";
import {
  identityOAuthClient,
  isOAuthRuntimeConfigurationError,
  oauthRuntimeMode,
  oauthService
} from "@/lib/oauth-service.server";
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
import {
  isOAuthIdentityCallbackUnavailable,
  isOAuthStoreUnavailable
} from "@/lib/redis-oauth-service";
import { configuredPublicOrigin } from "@/lib/public-origin";
import { DomainApiError, isDomainApiConfigured, syncDomainIdentity } from "@/lib/internal-api-client.server";
import { ONBOARD_SELLER_CSRF_ACTION } from "@/lib/mutation-protection.server";
import { demoProfileForSubject } from "@/lib/demo-config";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  try {
    const mode = oauthRuntimeMode();
    const callbackInput = {
      transactionId: request.cookies.get(transactionCookieName())?.value,
      code: request.nextUrl.searchParams.get("code") ?? undefined,
      state: request.nextUrl.searchParams.get("state") ?? undefined
    };
    const sessionId = mode === "identity"
      ? await oauthService().callbackIdentity(callbackInput, identityOAuthClient())
      : await oauthService().callback(callbackInput);
    const session = await oauthService().getSession(sessionId);
    if (!session) throw new DomainApiError(503, "session_unavailable_after_callback");
    if (isDomainApiConfigured()) {
      try {
        await syncDomainIdentity(sessionId, session);
      } catch (error) {
        await oauthService().logout(sessionId);
        throw error;
      }
    }
    const response = NextResponse.redirect(new URL(
      demoProfileForSubject(session.subject)?.home ?? "/dashboard",
      configuredPublicOrigin()
    ));
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
    const configurationError = isOAuthRuntimeConfigurationError(error) ||
      error instanceof IdentityOAuthConfigurationError;
    const unavailable = error instanceof IdentityOAuthUnavailableError;
    const ambiguous = error instanceof IdentityOAuthAmbiguousError ||
      isOAuthIdentityCallbackUnavailable(error);
    const response = NextResponse.json({
      error: configurationError ? "identity_not_configured" :
        ambiguous ? "identity_callback_ambiguous" :
        unavailable ? "identity_unavailable" :
        isOAuthStoreUnavailable(error) ? "oauth_store_unavailable" :
        error instanceof DomainApiError ? error.code : "invalid_oauth_callback"
    }, {
      status: configurationError || ambiguous || unavailable || isOAuthStoreUnavailable(error) || error instanceof DomainApiError
        ? 503
        : 400,
      headers: { "cache-control": "no-store" }
    });
    if (configurationError) expireTransactionCookie(response);
    return response;
  }
}

function expireTransactionCookie(response: NextResponse) {
  response.cookies.set(transactionCookieName(), "", {
    ...transactionCookieOptions(),
    maxAge: 0
  });
}
