import { NextResponse } from "next/server";
import { OAuthFlowError } from "@/lib/oauth-service";
import { oauthService } from "@/lib/oauth-service.server";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";
import { configuredPublicOrigin } from "@/lib/public-origin";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  if (process.env.NODE_ENV === "production") {
    return new NextResponse(null, { status: 404, headers: { "cache-control": "no-store" } });
  }
  try {
    const url = new URL(request.url);
    if (url.searchParams.get("code_challenge_method") !== "S256") throw new OAuthFlowError("Only PKCE S256 is supported.");
    const state = url.searchParams.get("state") ?? "";
    const code = await oauthService().authorize({
      state,
      nonce: url.searchParams.get("nonce") ?? "",
      codeChallenge: url.searchParams.get("code_challenge") ?? ""
    }, "local");
    const callback = new URL("/auth/callback", configuredPublicOrigin());
    callback.searchParams.set("code", code);
    callback.searchParams.set("state", state);
    const response = NextResponse.redirect(callback);
    response.headers.set("cache-control", "no-store");
    return response;
  } catch (error) {
    return NextResponse.json({
      error: isOAuthStoreUnavailable(error) ? "oauth_store_unavailable" : "invalid_authorization_request"
    }, {
      status: isOAuthStoreUnavailable(error) ? 503 : 400,
      headers: { "cache-control": "no-store" }
    });
  }
}
