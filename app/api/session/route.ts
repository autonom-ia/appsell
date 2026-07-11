import { NextRequest, NextResponse } from "next/server";
import { oauthService } from "@/lib/oauth-service.server";
import { sessionCookieName } from "@/lib/cookies";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  if (process.env.NODE_ENV === "production") {
    return NextResponse.json({ authenticated: false }, {
      status: 401,
      headers: { "cache-control": "no-store" }
    });
  }
  try {
    const sessionId = request.cookies.get(sessionCookieName())?.value;
    const session = await oauthService().getSession(sessionId);
    return NextResponse.json(session ? {
      authenticated: true,
      expiresAt: session.expiresAt
    } : { authenticated: false }, {
      status: session ? 200 : 401,
      headers: { "cache-control": "no-store" }
    });
  } catch (error) {
    if (!isOAuthStoreUnavailable(error)) throw error;
    return NextResponse.json({ authenticated: false, error: "oauth_store_unavailable" }, {
      status: 503,
      headers: { "cache-control": "no-store" }
    });
  }
}
