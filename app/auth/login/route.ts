import { NextResponse } from "next/server";
import { oauthService } from "@/lib/oauth-service.server";
import { transactionCookieName, transactionCookieOptions } from "@/lib/cookies";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";
import { configuredPublicOrigin } from "@/lib/public-origin";

export const dynamic = "force-dynamic";

export async function GET() {
  if (process.env.NODE_ENV === "production") {
    return NextResponse.json({ error: "identity_not_configured" }, {
      status: 503,
      headers: { "cache-control": "no-store" }
    });
  }
  try {
    const login = await oauthService().beginLogin("local");
    const authorize = new URL("/dev/identity/authorize", configuredPublicOrigin());
    authorize.searchParams.set("state", login.state);
    authorize.searchParams.set("nonce", login.nonce);
    authorize.searchParams.set("code_challenge", login.codeChallenge);
    authorize.searchParams.set("code_challenge_method", "S256");
    const response = NextResponse.redirect(authorize);
    response.cookies.set(transactionCookieName(), login.transactionId, transactionCookieOptions());
    response.headers.set("cache-control", "no-store");
    return response;
  } catch (error) {
    if (!isOAuthStoreUnavailable(error)) throw error;
    return NextResponse.json({ error: "oauth_store_unavailable" }, {
      status: 503,
      headers: { "cache-control": "no-store" }
    });
  }
}
