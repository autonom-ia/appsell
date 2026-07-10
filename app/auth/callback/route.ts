import { NextRequest, NextResponse } from "next/server";
import { oauthService } from "@/lib/oauth-service.server";
import {
  sessionCookieName,
  sessionCookieOptions,
  transactionCookieName,
  transactionCookieOptions
} from "@/lib/cookies";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  if (process.env.NODE_ENV === "production") {
    const response = NextResponse.json({ error: "identity_not_configured" }, {
      status: 503,
      headers: { "cache-control": "no-store" }
    });
    expireTransactionCookie(response);
    return response;
  }
  try {
    const sessionId = oauthService().callback({
      transactionId: request.cookies.get(transactionCookieName())?.value,
      code: request.nextUrl.searchParams.get("code") ?? undefined,
      state: request.nextUrl.searchParams.get("state") ?? undefined
    });
    const response = NextResponse.redirect(new URL("/dashboard", request.url));
    expireTransactionCookie(response);
    response.cookies.set(sessionCookieName(), sessionId, sessionCookieOptions());
    response.headers.set("cache-control", "no-store");
    return response;
  } catch {
    return NextResponse.json({ error: "invalid_oauth_callback" }, {
      status: 400,
      headers: { "cache-control": "no-store" }
    });
  }
}

function expireTransactionCookie(response: NextResponse) {
  response.cookies.set(transactionCookieName(), "", {
    ...transactionCookieOptions(),
    maxAge: 0
  });
}
