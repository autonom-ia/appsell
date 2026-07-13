import { NextRequest, NextResponse } from "next/server";

import {
  demoInspectionCookieName,
  demoInspectionCookieOptions,
  sessionCookieName
} from "@/lib/cookies";
import { parseDemoInspectionTicket } from "@/lib/demo-inspection-ticket";
import { DomainApiError, revokeDomainDemoInspection } from "@/lib/internal-api-client.server";
import { PayloadTooLargeError, readLimitedForm } from "@/lib/limited-form.server";
import {
  REVOKE_DEMO_INSPECTION_CSRF_ACTION,
  verifyMutation
} from "@/lib/mutation-protection.server";
import { oauthService } from "@/lib/oauth-service.server";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";

export async function POST(request: NextRequest) {
  if (process.env.NODE_ENV === "production") return jsonError(404, "not_found");
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId || !hasConfiguredOrigin(request, configuredPublicOrigin())) return forbidden();
  const ticket = parseDemoInspectionTicket(request.cookies.get(demoInspectionCookieName())?.value);
  if (!ticket) return jsonError(404, "inspection_not_found");

  let form: FormData | null;
  try {
    form = await readLimitedForm(request);
  } catch (error) {
    if (error instanceof PayloadTooLargeError) return jsonError(413, "payload_too_large");
    form = null;
  }
  const csrf = stringValue(form?.get("_csrf"));
  if (!verifyMutation(request, sessionId, REVOKE_DEMO_INSPECTION_CSRF_ACTION, csrf)) {
    return forbidden();
  }
  const reason = stringValue(form?.get("reason"));
  if (!validReason(reason)) return jsonError(422, "invalid_inspection_request");

  try {
    const session = await oauthService().getSession(sessionId);
    if (!session) return jsonError(401, "unauthorized");
    await revokeDomainDemoInspection(sessionId, session, ticket.id, ticket.token, reason);
    const response = NextResponse.redirect(
      new URL("/platform/demo-inspection?status=revoked", configuredPublicOrigin()),
      303
    );
    response.cookies.set(demoInspectionCookieName(), "", {
      ...demoInspectionCookieOptions(),
      maxAge: 0
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
