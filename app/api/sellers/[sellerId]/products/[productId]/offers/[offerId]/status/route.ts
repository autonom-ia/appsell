import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import { transitionDomainOfferStatus } from "@/lib/internal-api-client.server";
import {
  TRANSITION_OFFER_STATUS_CSRF_ACTION,
  verifyMutation
} from "@/lib/mutation-protection.server";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";

import {
  authenticatedSession,
  canonicalUuid,
  forbiddenMutation,
  invalidInput,
  isRecord,
  mappedError,
  payloadTooLarge,
  readExactLimitedFields,
  safeVersion,
  unauthorized
} from "../../../_route-shared";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const OFFER_STATUSES = new Set(["draft", "active", "archived"]);
const TRANSITION_FIELDS = [
  "_csrf",
  "idempotency_key",
  "expected_status",
  "status",
  "expected_offer_version"
] as const;

interface OfferStatusRouteContext {
  params: Promise<{ sellerId: string; productId: string; offerId: string }>;
}

export async function PATCH(request: NextRequest, context: OfferStatusRouteContext) {
  const { sellerId, productId, offerId } = await context.params;
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (
    !UUID.test(sellerId)
    || !UUID.test(productId)
    || !UUID.test(offerId)
    || !sessionId
    || !hasConfiguredOrigin(request, configuredPublicOrigin())
  ) {
    return forbiddenMutation();
  }

  let authenticated: Awaited<ReturnType<typeof authenticatedSession>>;
  try {
    authenticated = await authenticatedSession(request);
  } catch (error) {
    return mappedError(error);
  }
  if (!authenticated) return unauthorized();

  const parsed = await readExactLimitedFields(request, TRANSITION_FIELDS);
  if (parsed.kind === "too_large") return payloadTooLarge();
  if (parsed.kind === "invalid") return invalidInput("form");
  const fields = parsed.fields;
  if (!verifyMutation(
    request,
    sessionId,
    TRANSITION_OFFER_STATUS_CSRF_ACTION,
    fields._csrf
  )) return forbiddenMutation();

  const idempotencyKey = canonicalUuid(fields.idempotency_key);
  const offerVersion = safeVersion(fields.expected_offer_version);
  if (!idempotencyKey) return invalidInput("form");
  if (offerVersion === null) return invalidInput("expected_offer_version");
  if (!validStatus(fields.expected_status)) return invalidInput("expected_status");
  if (!validStatus(fields.status)) return invalidInput("status");
  if (fields.expected_status === fields.status) return invalidInput("status");

  try {
    const payload = await transitionDomainOfferStatus(
      authenticated.sessionId,
      authenticated.session,
      sellerId,
      productId,
      offerId,
      {
        idempotencyKey,
        expectedStatus: fields.expected_status,
        status: fields.status,
        expectedOfferVersion: offerVersion
      }
    );
    if (!isRecord(payload)) return mappedError(new Error("invalid domain response"));
    return NextResponse.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}

function validStatus(value: string | undefined): value is "draft" | "active" | "archived" {
  return value !== undefined && OFFER_STATUSES.has(value);
}
