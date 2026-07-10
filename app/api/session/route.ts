import { NextRequest, NextResponse } from "next/server";
import { oauthService } from "@/lib/oauth-service.server";
import { sessionCookieName } from "@/lib/cookies";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  if (process.env.NODE_ENV === "production") {
    return NextResponse.json({ authenticated: false }, {
      status: 401,
      headers: { "cache-control": "no-store" }
    });
  }
  const session = oauthService().getSession(request.cookies.get(sessionCookieName())?.value);
  return NextResponse.json(session ? { authenticated: true, ...session } : { authenticated: false }, {
    status: session ? 200 : 401,
    headers: { "cache-control": "no-store" }
  });
}
