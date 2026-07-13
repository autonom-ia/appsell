import { NextRequest, NextResponse } from "next/server";
import { sessionCookieName } from "./lib/cookies";
import { canAccessDemoPreview } from "./lib/demo-view";
import { oauthService } from "./lib/oauth-service.server";
import { demoProfileForSubject } from "./lib/demo-config";

export async function proxy(request: NextRequest) {
  if (process.env.NODE_ENV === "production") {
    return NextResponse.redirect(new URL("/", request.url));
  }

  try {
    const session = await oauthService().getSession(request.cookies.get(sessionCookieName())?.value);
    if (!canAccessDemoPreview(session?.subject, process.env.NODE_ENV, process.env)) {
      return NextResponse.redirect(new URL("/", request.url));
    }
    const demoProfile = demoProfileForSubject(session?.subject);
    if (demoProfile?.persona === "superadmin" &&
        !request.nextUrl.pathname.startsWith("/platform/")) {
      return NextResponse.redirect(new URL("/platform/demo-inspection", request.url));
    }
    if (demoProfile?.persona === "buyer" && !request.nextUrl.pathname.startsWith("/library")) {
      return NextResponse.redirect(new URL("/library", request.url));
    }
    return NextResponse.next();
  } catch {
    return NextResponse.redirect(new URL("/?error=session_unavailable", request.url));
  }
}

export const config = {
  matcher: ["/dashboard/:path*", "/products/:path*", "/library/:path*", "/platform/:path*"],
};
