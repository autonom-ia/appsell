import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import { setDomainDefaultOffer } from "@/lib/internal-api-client.server";
import {
  SET_DEFAULT_OFFER_CSRF_ACTION,
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
  productRouteIds,
  readExactLimitedFields,
  safeVersion,
  unauthorized
} from "../../../_route-shared";

// Troca a oferta padrão do produto (#553). A api recusa loja fora da lista e
// troca que mude o preço de um produto à venda; o BFF repassa a recusa.
const DEFAULT_FIELDS = ["_csrf", "idempotency_key", "expected_offer_version"] as const;

interface DefaultOfferRouteContext {
  params: Promise<{ sellerId: string; productId: string; offerId: string }>;
}

export async function POST(request: NextRequest, context: DefaultOfferRouteContext) {
  const { offerId: rawOfferId } = await context.params;
  const ids = await productRouteIds(context);
  const offerId = canonicalUuid(rawOfferId);
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!ids || !offerId || !sessionId || !hasConfiguredOrigin(request, configuredPublicOrigin())) {
    return forbiddenMutation();
  }

  let authenticated: Awaited<ReturnType<typeof authenticatedSession>>;
  try {
    authenticated = await authenticatedSession(request);
  } catch (error) {
    return mappedError(error);
  }
  if (!authenticated) return unauthorized();

  const parsed = await readExactLimitedFields(request, DEFAULT_FIELDS);
  if (parsed.kind === "too_large") return payloadTooLarge();
  if (parsed.kind === "invalid") return invalidInput("form");
  const fields = parsed.fields;
  if (!verifyMutation(request, sessionId, SET_DEFAULT_OFFER_CSRF_ACTION, fields._csrf)) {
    return forbiddenMutation();
  }

  const idempotencyKey = canonicalUuid(fields.idempotency_key);
  const offerVersion = safeVersion(fields.expected_offer_version);
  if (!idempotencyKey) return invalidInput("form");
  if (offerVersion === null) return invalidInput("expected_offer_version");

  try {
    const payload = await setDomainDefaultOffer(
      authenticated.sessionId,
      authenticated.session,
      ids.sellerId,
      ids.productId,
      offerId,
      { idempotencyKey, expectedOfferVersion: offerVersion }
    );
    if (!isRecord(payload)) return mappedError(new Error("invalid domain response"));
    return NextResponse.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}
