import { NextRequest, NextResponse } from "next/server";

import {
  demoInspectionCookieName,
  demoInspectionCookieOptions,
  sessionCookieName
} from "@/lib/cookies";
import {
  parseDemoInspectionTicket,
  serializeDemoInspectionTicket
} from "@/lib/demo-inspection-ticket";
import {
  DomainApiError,
  readDomainDemoInspection,
  readDomainIdentity,
  startDomainDemoInspection
} from "@/lib/internal-api-client.server";
import { PayloadTooLargeError, readLimitedForm } from "@/lib/limited-form.server";
import {
  REVOKE_DEMO_INSPECTION_CSRF_ACTION,
  START_DEMO_INSPECTION_CSRF_ACTION,
  csrfTokenForSession,
  verifyMutation
} from "@/lib/mutation-protection.server";
import { oauthService } from "@/lib/oauth-service.server";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";

export async function GET(request: NextRequest) {
  if (process.env.NODE_ENV === "production") return unavailable();
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId) return jsonError(401, "unauthorized");
  try {
    const session = await oauthService().getSession(sessionId);
    if (!session) return jsonError(401, "unauthorized");
    const identity = await readDomainIdentity(sessionId, session);
    if (!hasPlatformSuperadminGrant(identity)) return jsonError(403, "platform_grant_required");
    const ticket = parseDemoInspectionTicket(
      request.cookies.get(demoInspectionCookieName())?.value
    );
    let inspection = null;
    let ticketStatus: "inactive" | "active" | "expired" = "inactive";
    if (ticket) {
      try {
        inspection = await readDomainDemoInspection(sessionId, session, ticket.id, ticket.token);
        ticketStatus = "active";
      } catch (error) {
        if (!(error instanceof DomainApiError && error.status === 404)) throw error;
        ticketStatus = "expired";
      }
    }
    return NextResponse.json({
      authorized: true,
      ticket_status: ticketStatus,
      inspection,
      csrf: {
        start: csrfTokenForSession(sessionId, START_DEMO_INSPECTION_CSRF_ACTION),
        revoke: csrfTokenForSession(sessionId, REVOKE_DEMO_INSPECTION_CSRF_ACTION)
      }
    }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}

export async function POST(request: NextRequest) {
  if (process.env.NODE_ENV === "production") return unavailable();
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId || !hasConfiguredOrigin(request, configuredPublicOrigin())) return forbidden();

  let form: FormData | null;
  try {
    form = await readLimitedForm(request);
  } catch (error) {
    if (error instanceof PayloadTooLargeError) return jsonError(413, "payload_too_large");
    form = null;
  }
  const csrf = stringValue(form?.get("_csrf"));
  if (!verifyMutation(request, sessionId, START_DEMO_INSPECTION_CSRF_ACTION, csrf)) {
    return forbidden();
  }
  const reason = stringValue(form?.get("reason"));
  const ttlSeconds = Number(stringValue(form?.get("ttl_seconds")));
  if (!validReason(reason) || !Number.isInteger(ttlSeconds) || ttlSeconds < 30 || ttlSeconds > 900) {
    return jsonError(422, "invalid_inspection_request");
  }

  try {
    const session = await oauthService().getSession(sessionId);
    if (!session) return jsonError(401, "unauthorized");
    const payload = await startDomainDemoInspection(sessionId, session, { reason, ttlSeconds });
    const serialized = serializeDemoInspectionTicket({
      id: payload.inspection_id,
      token: payload.inspection_token
    });
    const response = NextResponse.redirect(
      new URL("/platform/demo-inspection?status=started", configuredPublicOrigin()),
      303
    );
    response.cookies.set(demoInspectionCookieName(), serialized, {
      ...demoInspectionCookieOptions(),
      maxAge: ttlSeconds
    });
    response.headers.set("cache-control", "no-store");
    return response;
  } catch (error) {
    return mappedError(error);
  }
}

function stringValue(value: FormDataEntryValue | null | undefined) {
  return typeof value === "string" ? value : "";
}

function validReason(value: string) {
  const bytes = Buffer.byteLength(value, "utf8");
  return value === value.trim() && bytes >= 8 && bytes <= 500 && !/[\p{Cc}\p{Cs}]/u.test(value);
}

function unavailable() {
  return jsonError(404, "not_found");
}

function forbidden() {
  return jsonError(403, "forbidden_mutation");
}

function jsonError(status: number, error: string) {
  return NextResponse.json({ error }, { status, headers: { "cache-control": "no-store" } });
}

function mappedError(error: unknown) {
  if (isOAuthStoreUnavailable(error)) return jsonError(503, "oauth_store_unavailable");
  if (error instanceof DomainApiError) return jsonError(error.status, error.code);
  return jsonError(503, "inspection_api_unavailable");
}

function hasPlatformSuperadminGrant(value: unknown) {
  return typeof value === "object" && value !== null && !Array.isArray(value) &&
    Array.isArray((value as Record<string, unknown>).platform_roles) &&
    ((value as Record<string, unknown>).platform_roles as unknown[]).includes("platform_superadmin");
}
