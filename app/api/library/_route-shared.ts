import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import { DomainApiError } from "@/lib/internal-api-client.server";
import { oauthService } from "@/lib/oauth-service.server";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";

export async function authenticatedLibrarySession(request: NextRequest) {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId) return null;
  const session = await oauthService().getSession(sessionId);
  return session ? { sessionId, session } : null;
}

export function libraryError(error: unknown) {
  const status = error instanceof DomainApiError ? error.status : 503;
  const code = error instanceof DomainApiError
    ? error.code
    : isOAuthStoreUnavailable(error)
      ? "oauth_store_unavailable"
      : "library_unavailable";
  return NextResponse.json({ error: code }, {
    status,
    headers: { "cache-control": "private, no-store" }
  });
}

export function libraryUnauthorized() {
  return NextResponse.json({ error: "unauthorized" }, {
    status: 401,
    headers: { "cache-control": "private, no-store" }
  });
}
