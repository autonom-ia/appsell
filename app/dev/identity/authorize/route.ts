import { NextResponse } from "next/server";
import { OAuthFlowError } from "@/lib/oauth-service";
import { oauthService } from "@/lib/oauth-service.server";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";
import { configuredPublicOrigin } from "@/lib/public-origin";
import { demoProfileForPersona } from "@/lib/demo-config";
import { publicDemoRuntimeEnabled } from "@/lib/public-demo-runtime";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  if (process.env.NODE_ENV === "production" && !publicDemoRuntimeEnabled()) {
    return new NextResponse(null, { status: 404, headers: { "cache-control": "no-store" } });
  }
  try {
    const url = new URL(request.url);
    if (url.searchParams.get("code_challenge_method") !== "S256") throw new OAuthFlowError("Only PKCE S256 is supported.");
    const state = url.searchParams.get("state") ?? "";
    const requestedPersona = url.searchParams.get("persona");
    const demoProfile = demoProfileForPersona(requestedPersona);
    if (requestedPersona && !demoProfile) throw new OAuthFlowError("Invalid demo persona.");
    const code = await oauthService().authorize({
      state,
      nonce: url.searchParams.get("nonce") ?? "",
      codeChallenge: url.searchParams.get("code_challenge") ?? ""
    }, "local", demoProfile?.subject ?? "dev-user-001");
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
