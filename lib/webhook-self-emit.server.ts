// NB: sem `import "server-only"` — este módulo é crypto puro + leitor de env,
// unit-testado (mesma convenção de lib/limited-*.server.ts). Só é importado por
// rotas/handlers server-side (checkout/complete, checkout-webhook-proxy).
import { createHash, createHmac } from "node:crypto";

import { isValidBuyerEmail, normalizeBuyerEmail } from "@/lib/buyer-email";

// Signs and shapes the checkout-completion webhook this BFF emits to itself
// (the buyer's browser never talks to the domain API's webhook route
// directly). The signing contract (canonical string, HMAC-SHA256, header
// names) mirrors apps/api/src/appsell_api/webhook_adapters.py exactly so the
// same ingress path (lib/checkout-webhook-proxy.server.ts →
// /v1/webhooks/checkouts/:endpointKey) accepts it.
//
// The domain ingress accepts BOTH the 4-key v1 payload and the 6-key v2
// payload below (adding buyer_email/buyer_identity_sub): webhook_adapters.py's
// `parse_generic_hmac_v1` matches `expected_v1 | {buyer_email,
// buyer_identity_sub}` and independently validates those fields. The v2 bridge
// is verified end-to-end at the code level (an earlier "KNOWN GAP" note here
// predated the domain API accepting v2 and was removed 2026-07-24).

export type CheckoutApprovedPayload = {
  event_type: "purchase.approved";
  external_product_id: string;
  external_purchase_id: string;
  occurred_at: string;
  buyer_email: string;
  buyer_identity_sub: string | null;
};

export interface SelfEmitWebhookHeaders {
  eventId: string;
  timestamp: string;
  signature: string;
}

export interface SelfEmitWebhookConfig {
  endpointKey: string;
  secret: Buffer;
}

const REGISTRATION_SUBJECT_PREFIX = "registration:";
// Same charset as webhook_adapters.py's `_valid_reference` (printable,
// no control characters), narrowed to the safe subset already used for our
// own identifiers elsewhere in this repo (internal-envelope event ids, etc).
const SAFE_REFERENCE = /^[A-Za-z0-9._:-]{1,128}$/;
const ENDPOINT_KEY = /^[A-Za-z0-9_-]{32,64}$/;

export class SelfEmitConfigError extends Error {}
export class SelfEmitPayloadError extends Error {}

export function selfEmitWebhookConfig(
  environment: Partial<Record<string, string | undefined>> = process.env
): SelfEmitWebhookConfig {
  const endpointKey = environment.APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY ?? "";
  if (!ENDPOINT_KEY.test(endpointKey)) {
    throw new SelfEmitConfigError("invalid_checkout_webhook_endpoint_key");
  }
  const secretB64 = environment.APPSELL_CHECKOUT_WEBHOOK_SECRET_B64 ?? "";
  const secret = Buffer.from(secretB64, "base64");
  if (secret.length !== 32 || secret.toString("base64") !== secretB64) {
    throw new SelfEmitConfigError("invalid_checkout_webhook_secret");
  }
  return { endpointKey, secret };
}

export function selfEmitEventId(orderId: string): string {
  // Deterministic per order: retries reuse the identical event id so the
  // domain API's replay handling returns "replayed" instead of creating a
  // duplicate purchase event.
  return `selfemit:${orderId}`;
}

export function buildCheckoutApprovedPayload(input: {
  financialProductKey: string;
  orderId: string;
  buyerEmail: string;
  buyerIdentitySub: string | null;
  occurredAtIso: string;
}): CheckoutApprovedPayload {
  if (!SAFE_REFERENCE.test(input.financialProductKey) || !SAFE_REFERENCE.test(input.orderId)) {
    throw new SelfEmitPayloadError("invalid_self_emit_reference");
  }
  const buyerEmail = normalizeBuyerEmail(input.buyerEmail);
  if (!isValidBuyerEmail(buyerEmail)) {
    throw new SelfEmitPayloadError("invalid_self_emit_email");
  }
  if (input.buyerIdentitySub !== null && input.buyerIdentitySub.startsWith(REGISTRATION_SUBJECT_PREFIX)) {
    // Defense in depth: the caller must already exclude anonymous
    // registration-checkout subjects, but this boundary re-checks it because
    // buyer_identity_sub ends up in an outbound, signed, third-party-visible
    // payload.
    throw new SelfEmitPayloadError("registration_subject_forbidden");
  }
  const occurredAtDate = new Date(input.occurredAtIso);
  if (Number.isNaN(occurredAtDate.getTime())) {
    throw new SelfEmitPayloadError("invalid_self_emit_occurred_at");
  }
  return {
    event_type: "purchase.approved",
    external_product_id: input.financialProductKey,
    external_purchase_id: input.orderId,
    occurred_at: occurredAtDate.toISOString(),
    buyer_email: buyerEmail,
    buyer_identity_sub: input.buyerIdentitySub
  };
}

export function serializeCheckoutPayload(payload: CheckoutApprovedPayload): Buffer {
  return Buffer.from(JSON.stringify(payload), "utf8");
}

export function signSelfEmitWebhook(input: {
  endpointKey: string;
  eventId: string;
  timestampSeconds: number;
  body: Buffer;
  secret: Buffer;
}): SelfEmitWebhookHeaders {
  if (input.secret.length !== 32) throw new SelfEmitConfigError("invalid_checkout_webhook_secret");
  const timestamp = String(input.timestampSeconds);
  const payloadHash = createHash("sha256").update(input.body).digest("hex");
  const canonical = [
    "appsell.webhook.generic-hmac.v1",
    input.endpointKey,
    timestamp,
    input.eventId,
    payloadHash
  ].join("\n");
  const signature = createHmac("sha256", input.secret).update(canonical).digest("hex");
  return { eventId: input.eventId, timestamp, signature: `v1=${signature}` };
}

