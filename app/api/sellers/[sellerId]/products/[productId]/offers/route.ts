import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import { createDomainProductOffer, readDomainProductDraft } from "@/lib/internal-api-client.server";
import {
  CREATE_PRODUCT_OFFER_CSRF_ACTION,
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
  type ProductRouteContext,
  readExactLimitedFields,
  safeVersion,
  unauthorized
} from "../_route-shared";
import { minimumForPrice, priceBelowMinimum, sellerChargesCommission } from "../_minimum-price";

// Segunda oferta do produto (#553). Quem decide se a loja pode, e se preço e
// moeda valem, é a api (Decisão 005): o BFF confere forma, sessão e CSRF e
// repassa os campos como vieram. A negação da api chega ao browser como veio.
const CREATE_FIELDS = [
  "_csrf",
  "idempotency_key",
  "offer_id",
  "title",
  "summary",
  "unit_amount_minor",
  "currency",
  "billing_period"
] as const;

export async function POST(request: NextRequest, context: ProductRouteContext) {
  const ids = await productRouteIds(context);
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

  const parsed = await readExactLimitedFields(request, CREATE_FIELDS);
  if (parsed.kind === "too_large") return payloadTooLarge();
  if (parsed.kind === "invalid") return invalidInput("form");
  const fields = parsed.fields;
  if (!verifyMutation(request, sessionId, CREATE_PRODUCT_OFFER_CSRF_ACTION, fields._csrf)) {
    return forbiddenMutation();
  }

  const idempotencyKey = canonicalUuid(fields.idempotency_key);
  const offerId = canonicalUuid(fields.offer_id);
  const unitAmountMinor = safeVersion(fields.unit_amount_minor, true);
  if (!idempotencyKey || !offerId) return invalidInput("form");
  if (unitAmountMinor === null) return invalidInput("unit_amount_minor");
  const minimumMinor = minimumForPrice(unitAmountMinor, fields.currency ?? "");

  try {
    // #546: oferta nova abaixo do mínimo, em loja com comissão, não é gravada.
    // Ler o rascunho antes confirma que a loja é de quem pede; só depois se
    // consulta a comissão, para o 422 não revelar nada de loja alheia.
    if (minimumMinor !== null) {
      await readDomainProductDraft(authenticated.sessionId, authenticated.session, ids.sellerId, ids.productId);
      if (await sellerChargesCommission(ids.sellerId)) return priceBelowMinimum(minimumMinor, "unit_amount_minor");
    }
    const payload = await createDomainProductOffer(
      authenticated.sessionId,
      authenticated.session,
      ids.sellerId,
      ids.productId,
      {
        idempotencyKey,
        offerId,
        title: fields.title ?? "",
        summary: fields.summary ?? "",
        unitAmountMinor,
        currency: fields.currency ?? "",
        billingPeriod: fields.billing_period ?? ""
      }
    );
    if (!isRecord(payload)) return mappedError(new Error("invalid domain response"));
    return NextResponse.json(payload, {
      status: payload.created === true ? 201 : 200,
      headers: { "cache-control": "no-store" }
    });
  } catch (error) {
    return mappedError(error);
  }
}
