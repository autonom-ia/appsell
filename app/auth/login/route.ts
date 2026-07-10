import { NextResponse } from "next/server";
import { oauthService } from "@/lib/oauth-service.server";
import { transactionCookieName, transactionCookieOptions } from "@/lib/cookies";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  if (process.env.NODE_ENV === "production") {
    return NextResponse.json({ error: "identity_not_configured" }, {
      status: 503,
      headers: { "cache-control": "no-store" }
    });
  }
  try {
    const login = await oauthService().beginLogin(clientKey(request));
    const authorize = new URL("/dev/identity/authorize", request.url);
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

function clientKey(request: Request) {
  return request.headers.get("x-forwarded-for")?.split(",", 1)[0]?.trim() || "local";
}
