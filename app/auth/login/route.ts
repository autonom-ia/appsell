import { NextRequest, NextResponse } from "next/server";
import { demoModeEnabled, demoProfileForPersona } from "@/lib/demo-config";
import { IdentityOAuthConfigurationError } from "@/lib/identity-oauth-client.server";
import {
  identityOAuthClient,
  isOAuthRuntimeConfigurationError,
  oauthRuntimeMode,
  oauthService
} from "@/lib/oauth-service.server";
import { transactionCookieName, transactionCookieOptions } from "@/lib/cookies";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";
import { configuredPublicOrigin } from "@/lib/public-origin";
import { signDemoPersonaBinding } from "@/lib/demo-persona-binding.server";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  try {
    const mode = oauthRuntimeMode();
    const requestedPersona = request.nextUrl.searchParams.get("persona");
    if (mode === "local" && demoModeEnabled() && !requestedPersona) {
      return NextResponse.json({ error: "demo_persona_required" }, {
        status: 400,
        headers: { "cache-control": "no-store" }
      });
    }
    if (mode === "identity" && requestedPersona) {
      return NextResponse.json({ error: "invalid_demo_persona" }, {
        status: 400,
        headers: { "cache-control": "no-store" }
      });
    }
    const demoProfile = demoProfileForPersona(requestedPersona);
    if (requestedPersona && demoModeEnabled() && !demoProfile) {
      return NextResponse.json({ error: "invalid_demo_persona" }, {
        status: 400,
        headers: { "cache-control": "no-store" }
      });
    }
    const login = await oauthService().beginLogin(mode);
    const authorize = mode === "identity"
      ? identityOAuthClient().authorizationUrl(login)
      : new URL("/dev/identity/authorize", configuredPublicOrigin());
    if (mode === "local") {
      authorize.searchParams.set("state", login.state);
      authorize.searchParams.set("nonce", login.nonce);
      authorize.searchParams.set("code_challenge", login.codeChallenge);
      authorize.searchParams.set("code_challenge_method", "S256");
      if (demoProfile) {
        authorize.searchParams.set("persona", demoProfile.persona);
        authorize.searchParams.set(
          "persona_sig",
          signDemoPersonaBinding(login.state, demoProfile.persona)
        );
      }
    }
    const response = NextResponse.redirect(authorize);
    response.cookies.set(transactionCookieName(), login.transactionId, transactionCookieOptions());
    response.headers.set("cache-control", "no-store");
    return response;
  } catch (error) {
    if (isOAuthRuntimeConfigurationError(error) || error instanceof IdentityOAuthConfigurationError) {
      return NextResponse.json({ error: "identity_not_configured" }, {
        status: 503,
        headers: { "cache-control": "no-store" }
      });
    }
    if (!isOAuthStoreUnavailable(error)) throw error;
    return NextResponse.json({ error: "oauth_store_unavailable" }, {
      status: 503,
      headers: { "cache-control": "no-store" }
    });
  }
}
