import { NextRequest, NextResponse } from "next/server";

import { revokeDomainAccessLink } from "@/lib/internal-api-client.server";
import {
  REVOKE_ACCESS_LINK_CSRF_ACTION,
  verifyMutation
} from "@/lib/mutation-protection.server";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";
import { sessionCookieName } from "@/lib/cookies";

import {
  authenticatedSession,
  canonicalUuid,
  forbiddenMutation,
  invalidInput,
  mappedError,
  payloadTooLarge,
  readExactLimitedFields,
  unauthorized
} from "../../../_route-shared";
import { accessLinkRouteIds, type AccessLinkRouteContext } from "../_shared";

const FIELDS = ["_csrf", "idempotency_key"] as const;

export async function POST(request: NextRequest, context: AccessLinkRouteContext) {
  const ids = await accessLinkRouteIds(context);
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!ids || !sessionId || !hasConfiguredOrigin(request, configuredPublicOrigin())) {
    return forbiddenMutation();
  }

  let authenticated: Awaited<ReturnType<typeof authenticatedSession>>;
  try {
    authenticated = await authenticatedSession(request);
  } catch (error) {
    return mappedError(error);
  }
  if (!authenticated) return unauthorized();

  const parsed = await readExactLimitedFields(request, FIELDS);
  if (parsed.kind === "too_large") return payloadTooLarge();
  if (parsed.kind === "invalid") return invalidInput("form");
  if (!verifyMutation(request, sessionId, REVOKE_ACCESS_LINK_CSRF_ACTION, parsed.fields._csrf)) {
    return forbiddenMutation();
  }
  const idempotencyKey = canonicalUuid(parsed.fields.idempotency_key);
  if (!idempotencyKey) return invalidInput("form");

  try {
    const payload = await revokeDomainAccessLink(
      authenticated.sessionId,
      authenticated.session,
      ids.sellerId,
      ids.productId,
      ids.accessLinkId,
      { idempotencyKey }
    );
    return NextResponse.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}
