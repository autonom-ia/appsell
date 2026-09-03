import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import { updateDomainProductHeadline } from "@/lib/internal-api-client.server";
import { UPDATE_PRODUCT_HEADLINE_CSRF_ACTION, verifyMutation } from "@/lib/mutation-protection.server";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";
import { STORE_LIMITS, normalizeOptionalText } from "@/lib/store-settings";
import {
  authenticatedSession,
  canonicalUuid,
  forbiddenMutation,
  invalidInput,
  mappedError,
  payloadTooLarge,
  productRouteIds,
  readExactLimitedFields,
  safeVersion,
  unauthorized,
  type ProductRouteContext
} from "../_route-shared";

// A promessa da página de vendas (#352). Campo do produto, salvo à parte do
// rascunho: tem versão esperada como os outros, mas não passa pelo formulário
// grande — trocar a frase não deve disputar com o autosave do nome e da
// descrição.

const REQUIRED = ["_csrf", "idempotency_key", "expected_product_version", "headline"] as const;
const MAX_FORM_BYTES = 2_048;
const CONTROL = /[\p{Cc}\p{Cs}]/u;

export async function PATCH(request: NextRequest, context: ProductRouteContext) {
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
  const parsed = await readExactLimitedFields(request, REQUIRED, MAX_FORM_BYTES);
  if (parsed.kind === "too_large") return payloadTooLarge();
  if (parsed.kind === "invalid") return invalidInput("form");
  if (!verifyMutation(request, sessionId, UPDATE_PRODUCT_HEADLINE_CSRF_ACTION, parsed.fields._csrf)) {
    return forbiddenMutation();
  }
  const idempotencyKey = canonicalUuid(parsed.fields.idempotency_key);
  const expectedProductVersion = safeVersion(parsed.fields.expected_product_version);
  if (!idempotencyKey || expectedProductVersion === null) return invalidInput("form");
  const headline = normalizeOptionalText(parsed.fields.headline);
  if (
    headline !== null &&
    (headline.length < STORE_LIMITS.headline.min || headline.length > STORE_LIMITS.headline.max || CONTROL.test(headline))
  ) return invalidInput("headline");
  try {
    const payload = await updateDomainProductHeadline(
      authenticated.sessionId,
      authenticated.session,
      { sellerOrganizationId: ids.sellerId, productId: ids.productId, idempotencyKey, expectedProductVersion, headline }
    );
    return NextResponse.json(payload, { status: 200, headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}
