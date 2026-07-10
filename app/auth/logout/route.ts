import { NextRequest, NextResponse } from "next/server";
import { isSameOrigin } from "@/lib/oauth-service";
import { oauthService } from "@/lib/oauth-service.server";
import { sessionCookieName, sessionCookieOptions } from "@/lib/cookies";

export async function POST(request: NextRequest) {
  if (!isSameOrigin(request)) {
    return NextResponse.json({ error: "forbidden_origin" }, {
      status: 403,
      headers: { "cache-control": "no-store" }
    });
  }
  if (process.env.NODE_ENV !== "production") {
    oauthService().logout(request.cookies.get(sessionCookieName())?.value);
  }
  const response = NextResponse.redirect(new URL("/", request.url), 303);
  response.cookies.set(sessionCookieName(), "", { ...sessionCookieOptions(), maxAge: 0 });
  response.headers.set("cache-control", "no-store");
  return response;
}
