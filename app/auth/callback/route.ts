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
  recoveryCookieName,
  recoveryCookieOptions,
  postLoginCookieName,
  postLoginCookieOptions,
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
import { shouldRestartLogin } from "@/lib/oauth-callback-recovery";
import { isBrowserNavigation, renderCallbackErrorPage } from "@/lib/oauth-callback-error-page";
import { configuredPublicOrigin } from "@/lib/public-origin";
import { describeCallbackFailure, reportOAuthCallbackFailure } from "@/lib/oauth-callback-telemetry";
import {
  DomainApiError,
  isDomainApiConfigured,
  readDomainIdentity,
  syncDomainIdentity
} from "@/lib/internal-api-client.server";
import { ONBOARD_SELLER_CSRF_ACTION } from "@/lib/mutation-protection.server";
import { demoProfileForSubject } from "@/lib/demo-config";
import { oauthCallbackHome } from "@/lib/oauth-callback-home";
import { safePostLoginPath } from "@/lib/post-login-redirect";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  let issuedSessionId: string | undefined;
  let demoSuperadmin = false;
  const callbackInput = {
    transactionId: request.cookies.get(transactionCookieName())?.value,
    code: request.nextUrl.searchParams.get("code") ?? undefined,
    state: request.nextUrl.searchParams.get("state") ?? undefined
  };
  // A bare hit on the endpoint (scanner, stale tab) carries none of the
  // identity-provider parameters and is not a failing login; recording those
  // would let any anonymous caller amplify the log at will. A real return does
  // carry them — including the failure shapes worth seeing, such as an expired
  // transaction cookie (code+state, no cookie) or `error=access_denied`. This
  // reduces noise without hiding genuine failures; it is not a rate limit, and
  // a caller willing to forge parameters can still reach the log.
  const looksLikeProviderReturn = Boolean(
    callbackInput.code || callbackInput.state || request.nextUrl.searchParams.get("error")
  );
  try {
    const mode = oauthRuntimeMode();
    const sessionId = mode === "identity"
      ? await oauthService().callbackIdentity(callbackInput, identityOAuthClient())
      : await oauthService().callback(callbackInput);
    issuedSessionId = sessionId;
    const session = await oauthService().getSession(sessionId);
    if (!session) throw new DomainApiError(503, "session_unavailable_after_callback");
    const demoProfile = demoProfileForSubject(session.subject);
    demoSuperadmin = demoProfile?.persona === "superadmin";
    if (demoSuperadmin && !isDomainApiConfigured()) {
      await oauthService().logout(sessionId);
      throw new DomainApiError(503, "domain_api_not_configured");
    }
    if (isDomainApiConfigured()) {
      try {
        await syncDomainIdentity(sessionId, session);
        if (demoProfile?.persona === "superadmin") {
          const platformSuperadmin = hasPlatformSuperadminGrant(
            await readDomainIdentity(sessionId, session)
          );
          if (!platformSuperadmin) {
            throw new DomainApiError(403, "demo_superadmin_grant_required");
          }
        }
      } catch (error) {
        await oauthService().logout(sessionId);
        throw error;
      }
    }
    const destination = safePostLoginPath(
      request.cookies.get(postLoginCookieName())?.value,
      configuredPublicOrigin()
    ) ?? oauthCallbackHome(demoProfile);
    const response = NextResponse.redirect(new URL(destination, configuredPublicOrigin()));
    expireTransactionCookie(response);
    response.cookies.set(postLoginCookieName(), "", { ...postLoginCookieOptions(), maxAge: 0 });
    // Clear the one-shot guard so a later expiry is recoverable again.
    response.cookies.set(recoveryCookieName(), "", { ...recoveryCookieOptions(), maxAge: 0 });
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
    const configurationError = isOAuthRuntimeConfigurationError(error) ||
      error instanceof IdentityOAuthConfigurationError;
    const unavailable = error instanceof IdentityOAuthUnavailableError;
    const ambiguous = error instanceof IdentityOAuthAmbiguousError ||
      isOAuthIdentityCallbackUnavailable(error);
    const outcome = configurationError ? "identity_not_configured" :
      ambiguous ? "identity_callback_ambiguous" :
      unavailable ? "identity_unavailable" :
      isOAuthStoreUnavailable(error) ? "oauth_store_unavailable" :
      error instanceof DomainApiError ? error.code : "invalid_oauth_callback";
    // The response stays coarse on purpose; the reason goes to the server log
    // so a misconfigured issuer/audience is diagnosable without exposing an
    // oracle to an anonymous caller.
    if (looksLikeProviderReturn) {
      reportOAuthCallbackFailure({ outcome, reason: describeCallbackFailure(error) });
    }
    // The browser came back from the provider but this app no longer holds the
    // login transaction — the usual cause is a sign-up, where creating the
    // account and confirming the e-mail takes longer than the transaction TTL.
    // Nothing is wrong with the account, so restart the login instead of
    // showing raw JSON: with the provider session still valid the round trip is
    // invisible and the user lands signed in. Guarded by a short-lived cookie
    // so a failure that survives the restart falls through to the error body
    // rather than looping.
    const alreadyRecovered = Boolean(request.cookies.get(recoveryCookieName())?.value);
    if (shouldRestartLogin(error, alreadyRecovered)) {
      const retryUrl = new URL("/auth/login", configuredPublicOrigin());
      const postLoginPath = safePostLoginPath(
        request.cookies.get(postLoginCookieName())?.value,
        configuredPublicOrigin()
      );
      if (postLoginPath) retryUrl.searchParams.set("next", postLoginPath);
      const retry = NextResponse.redirect(retryUrl);
      expireTransactionCookie(retry);
      retry.cookies.set(recoveryCookieName(), "1", recoveryCookieOptions());
      retry.headers.set("cache-control", "no-store");
      return retry;
    }
    const status = error instanceof DomainApiError
      ? error.status
      : configurationError || ambiguous || unavailable || isOAuthStoreUnavailable(error) ? 503 : 400;
    // A browser gets a readable page with a way back to the login; API clients
    // keep the machine-readable body. The page text is identical for every
    // outcome so it cannot be used to probe configuration.
    const response = isBrowserNavigation(request.headers.get("accept"))
      ? new NextResponse(renderCallbackErrorPage(outcome), {
        status,
        headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" }
      })
      : NextResponse.json({ error: outcome }, {
        status,
        headers: { "cache-control": "no-store" }
      });
    if (issuedSessionId) {
      expireTransactionCookie(response);
      response.cookies.set(sessionCookieName(), "", { ...sessionCookieOptions(), maxAge: 0 });
      if (demoSuperadmin) expireSellerCookies(response);
    } else if (configurationError) {
      expireTransactionCookie(response);
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
