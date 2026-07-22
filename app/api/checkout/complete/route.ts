import { NextRequest, NextResponse } from "next/server";

import { isPendingRegistrationCheckoutOrder, parsePaidRegistrationCheckoutOrder } from "@/lib/checkout-order";
import { proxyCheckoutWebhook } from "@/lib/checkout-webhook-proxy.server";
import { sessionCookieName } from "@/lib/cookies";
import { FinancialOrderFetchError, fetchRegistrationCheckoutOrder } from "@/lib/financial-order-client.server";
import { RawBodyTooLargeError, readLimitedRawBody } from "@/lib/limited-raw-body.server";
import { oauthService } from "@/lib/oauth-service.server";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";
import {
  SelfEmitPayloadError,
  buildCheckoutApprovedPayload,
  selfEmitEventId,
  selfEmitWebhookConfig,
  serializeCheckoutPayload,
  signSelfEmitWebhook
} from "@/lib/webhook-self-emit.server";

export const dynamic = "force-dynamic";

const MAX_BODY_BYTES = 4_096;
// event_id do ingress = 'selfemit:'(9) + order_id, cap 128 → order_id <= 119.
const ORDER_ID = /^[A-Za-z0-9._:-]{1,119}$/;
// Anonymous registration-checkout sessions mint subjects with this prefix
// (see app/api/registration-checkout); the completed-order webhook must
// never carry one as buyer_identity_sub — only a real authenticated
// (non-registration) session subject, or null.
const REGISTRATION_SUBJECT_PREFIX = "registration:";

export async function POST(request: NextRequest) {
  if (!hasConfiguredOrigin(request, configuredPublicOrigin())) return jsonError("forbidden_origin", 403);

  let rawBody: Buffer;
  try {
    rawBody = await readLimitedRawBody(request, MAX_BODY_BYTES);
  } catch (error) {
    return error instanceof RawBodyTooLargeError ? jsonError("payload_too_large", 413) : jsonError("invalid_request", 400);
  }

  const orderId = parseOrderIdBody(rawBody);
  if (!orderId) return jsonError("invalid_request", 400);

  let orderPayload: unknown;
  try {
    orderPayload = await fetchRegistrationCheckoutOrder(orderId);
  } catch (error) {
    if (error instanceof FinancialOrderFetchError && error.status === 404) return jsonError("order_not_found", 404);
    return jsonError("checkout_unavailable", 503);
  }

  const order = parsePaidRegistrationCheckoutOrder(orderId, orderPayload);
  if (!order) {
    if (isPendingRegistrationCheckoutOrder(orderId, orderPayload)) return jsonError("order_not_paid", 402);
    return jsonError("checkout_unavailable", 503);
  }

  const buyerIdentitySub = await resolveBuyerIdentitySub(request, order.buyerEmail);

  let config: { endpointKey: string; secret: Buffer };
  try {
    config = selfEmitWebhookConfig();
  } catch {
    return jsonError("checkout_webhook_not_configured", 503);
  }

  let payloadBuffer: Buffer;
  try {
    const payload = buildCheckoutApprovedPayload({
      financialProductKey: order.financialProductKey,
      orderId: order.orderId,
      buyerEmail: order.buyerEmail,
      buyerIdentitySub,
      occurredAtIso: order.occurredAt
    });
    payloadBuffer = serializeCheckoutPayload(payload);
  } catch (error) {
    if (error instanceof SelfEmitPayloadError) return jsonError("checkout_webhook_payload_invalid", 500);
    throw error;
  }

  const eventId = selfEmitEventId(order.orderId);
  const headers = signSelfEmitWebhook({
    endpointKey: config.endpointKey,
    eventId,
    timestampSeconds: Math.floor(Date.now() / 1_000),
    body: payloadBuffer,
    secret: config.secret
  });

  try {
    const delivered = await proxyCheckoutWebhook(config.endpointKey, payloadBuffer, headers);
    if (delivered.status === 202 && isRecord(delivered.payload)) {
      const status = delivered.payload.status;
      if (status === "accepted" || status === "replayed") {
        return NextResponse.json({ status }, { status: 200, headers: noStore() });
      }
    }
    // 409 = event id já ingerido com payload divergente (ex.: tentativa anterior
    // anônima, agora autenticada). O pedido já foi fulfilled — idempotente.
    if (delivered.status === 409) {
      return NextResponse.json({ status: "replayed" }, { status: 200, headers: noStore() });
    }
    if (delivered.status === 404) return jsonError("order_not_found", 404);
    return jsonError("checkout_webhook_rejected", 503);
  } catch {
    return jsonError("checkout_webhook_unavailable", 503);
  }
}

// Só vincula o comprador à identidade autenticada quando o e-mail VERIFICADO
// da sessão bate com o e-mail do PEDIDO (verificado no Financial). Sem essa
// checagem, quem tivesse um order_id pago poderia amarrá-lo à própria sessão
// (a migration 0020 prioriza o sub explícito). Se não bater (ou anônimo),
// emite null → o worker deriva registration:<email do pedido>. Determinístico:
// mesmo pedido + mesma sessão => mesmo sub.
async function resolveBuyerIdentitySub(
  request: NextRequest,
  orderBuyerEmail: string
): Promise<string | null> {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId) return null;
  try {
    const session = await oauthService().getSession(sessionId);
    const subject = session?.subject;
    const identity = session?.identity;
    if (typeof subject !== "string" || subject.length === 0) return null;
    if (subject.startsWith(REGISTRATION_SUBJECT_PREFIX)) return null;
    if (
      !identity
      || identity.emailVerified !== true
      || typeof identity.email !== "string"
      || identity.email.trim().toLowerCase() !== orderBuyerEmail.trim().toLowerCase()
    ) {
      return null;
    }
    return subject;
  } catch {
    // OAuth/session infra errors (e.g. Redis unavailable) must not block an
    // otherwise-anonymous checkout completion — fall back to no identity.
    return null;
  }
}

function parseOrderIdBody(rawBody: Buffer): string | null {
  if (rawBody.length === 0) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawBody.toString("utf8"));
  } catch {
    return null;
  }
  if (!isRecord(parsed) || !hasExactKeys(parsed, ["order_id"])) return null;
  const orderId = parsed.order_id;
  return typeof orderId === "string" && ORDER_ID.test(orderId) ? orderId : null;
}

function hasExactKeys<T extends string>(value: Record<string, unknown>, expected: readonly T[]): value is Record<T, unknown> {
  const actual = Object.keys(value).sort();
  const keys = [...expected].sort();
  return actual.length === keys.length && actual.every((key, index) => key === keys[index]);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function noStore() {
  return { "cache-control": "private, no-store" };
}

function jsonError(error: string, status: number) {
  return NextResponse.json({ error }, { status, headers: noStore() });
}
