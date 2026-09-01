import "server-only";

import { NextRequest, NextResponse } from "next/server";
import { demoModeEnabled, demoProfileForPersona } from "@/lib/demo-config";
import { IdentityOAuthConfigurationError } from "@/lib/identity-oauth-client.server";
import {
  identityOAuthClient,
  isOAuthRuntimeConfigurationError,
  oauthRuntimeMode,
  oauthService
} from "@/lib/oauth-service.server";
import { postLoginCookieName, postLoginCookieOptions, transactionCookieName, transactionCookieOptions } from "@/lib/cookies";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";
import { configuredPublicOrigin } from "@/lib/public-origin";
import { signDemoPersonaBinding } from "@/lib/demo-persona-binding.server";
import { safePostLoginPath } from "@/lib/post-login-redirect";

// Por onde o visitante entra no Identity. Os dois caminhos abrem a mesma
// transacao OAuth e trocam apenas a tela apresentada: "authorize" cai no login,
// "register" cai no formulario de criacao de conta. O Identity le client_id,
// redirect_uri, state e code_challenge da propria query string, entao o pedido
// sobrevive nos dois casos.
export type AuthEntry = "authorize" | "register";

export async function beginAuthEntry(request: NextRequest, entry: AuthEntry) {
  try {
    const mode = oauthRuntimeMode();
    const postLoginPath = safePostLoginPath(
      request.nextUrl.searchParams.get("next"),
      configuredPublicOrigin()
    );
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
    const destination = mode === "identity"
      ? (entry === "register"
        ? identityOAuthClient().registrationUrl(login)
        : identityOAuthClient().authorizationUrl(login))
      : new URL("/dev/identity/authorize", configuredPublicOrigin());
    if (mode === "local") {
      destination.searchParams.set("state", login.state);
      destination.searchParams.set("nonce", login.nonce);
      destination.searchParams.set("code_challenge", login.codeChallenge);
      destination.searchParams.set("code_challenge_method", "S256");
      if (demoProfile) {
        destination.searchParams.set("persona", demoProfile.persona);
        destination.searchParams.set(
          "persona_sig",
          signDemoPersonaBinding(login.state, demoProfile.persona)
        );
      }
    }
    const response = NextResponse.redirect(destination);
    response.cookies.set(transactionCookieName(), login.transactionId, transactionCookieOptions());
    if (postLoginPath) {
      response.cookies.set(postLoginCookieName(), postLoginPath, postLoginCookieOptions());
    }
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
