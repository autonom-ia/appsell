import { timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import { identityDemoModeEnabled } from "@/lib/demo-config";
import { identityOAuthClient, oauthService } from "@/lib/oauth-service.server";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  if (process.env.NODE_ENV === "production" || !identityDemoModeEnabled()) {
    return json({ error: "not_found" }, 404);
  }
  if (!validControlToken(request.headers.get("x-autonomia-e2e-control"))) {
    return json({ error: "forbidden" }, 403);
  }
  try {
    const sessionId = request.cookies.get(sessionCookieName())?.value;
    const session = await oauthService().getSession(sessionId);
    if (!session) return json({ error: "unauthorized" }, 401);
    const expectedRevision = Number(request.headers.get("x-autonomia-e2e-expected-revision"));
    if (!Number.isSafeInteger(expectedRevision) || expectedRevision < 1) {
      return json({ error: "invalid_revision" }, 422);
    }
    const refreshed = await oauthService().refreshSession(
      sessionId,
      expectedRevision,
      identityOAuthClient()
    );
    return json({ refreshed: true, revision: refreshed.revision }, 200);
  } catch (error) {
    return json({
      error: isOAuthStoreUnavailable(error) ? "oauth_store_unavailable" : "identity_refresh_failed"
    }, 503);
  }
}

function validControlToken(received: string | null) {
  const expected = process.env.APPSELL_IDENTITY_E2E_CONTROL_TOKEN;
  if (!expected || expected.length < 32 || !received || received.length !== expected.length) return false;
  return timingSafeEqual(Buffer.from(received), Buffer.from(expected));
}

function json(body: Record<string, unknown>, status: number) {
  return NextResponse.json(body, {
    status,
    headers: { "cache-control": "no-store" }
  });
}
