import { NextRequest, NextResponse } from "next/server";
import type Stripe from "stripe";

import { proxyCheckoutWebhook } from "@/lib/checkout-webhook-proxy.server";
import { RawBodyTooLargeError, readLimitedRawBody } from "@/lib/limited-raw-body.server";
import { StripeConfigError, stripeClient, stripeWebhookSecret } from "@/lib/stripe-config.server";
import {
  SelfEmitPayloadError,
  buildCheckoutApprovedPayload,
  selfEmitEventId,
  selfEmitWebhookConfig,
  serializeCheckoutPayload,
  signSelfEmitWebhook
} from "@/lib/webhook-self-emit.server";

// Real Stripe webhook → existing signed self-emit bridge. The only thing that
// changes versus app/api/checkout/complete/route.ts is the TRIGGER: instead of
// polling Financial for a paid order, a genuine, signature-verified Stripe
// `checkout.session.completed` event drives the same
// buildCheckoutApprovedPayload → signSelfEmitWebhook → proxyCheckoutWebhook path
// that grants course access. Slice 1 is platform-direct, so buyer_identity_sub
// is always null (the domain worker derives registration:<email>).
export const dynamic = "force-dynamic";

// Stripe events are small, but the domain ingress caps bodies at 64KB; keep the
// same ceiling so an oversized event is rejected here, not deep in the proxy.
const MAX_BODY_BYTES = 64 * 1024;
const SIGNATURE_HEADER = "stripe-signature";

export async function POST(request: NextRequest) {
  // Read the EXACT bytes: Stripe recomputes the HMAC over the raw payload, so a
  // re-serialized JSON object would fail verification.
  let rawBody: Buffer;
  try {
    rawBody = await readLimitedRawBody(request, MAX_BODY_BYTES);
  } catch (error) {
    return error instanceof RawBodyTooLargeError ? jsonError("payload_too_large", 413) : jsonError("invalid_request", 400);
  }

  const signature = request.headers.get(SIGNATURE_HEADER);
  if (!signature) return jsonError("missing_signature", 400);

  // MANDATORY, non-negotiable: verify with the SDK (never hand-rolled HMAC).
  let event: Stripe.Event;
  try {
    const client = stripeClient();
    event = client.webhooks.constructEvent(rawBody, signature, stripeWebhookSecret());
  } catch (error) {
    if (error instanceof StripeConfigError) return jsonError("stripe_not_configured", 503);
    // Signature mismatch / malformed payload — Stripe throws here.
    return jsonError("invalid_signature", 400);
  }

  // Acknowledge everything else with 200 so Stripe stops retrying events we
  // intentionally do not act on.
  if (event.type !== "checkout.session.completed") {
    return NextResponse.json({ received: true }, { status: 200, headers: noStore() });
  }

  const session = event.data.object as Stripe.Checkout.Session;
  // Only a fully paid session grants access. `complete`/`open` sessions or
  // `unpaid`/`no_payment_required` are acknowledged without emitting.
  if (session.payment_status !== "paid") {
    return NextResponse.json({ received: true, ignored: "not_paid" }, { status: 200, headers: noStore() });
  }

  const metadata = readCheckoutMetadata(session.metadata);
  if (!metadata) return jsonError("invalid_session_metadata", 400);

  let config: { endpointKey: string; secret: Buffer };
  try {
    config = selfEmitWebhookConfig();
  } catch {
    return jsonError("checkout_webhook_not_configured", 503);
  }

  let payloadBuffer: Buffer;
  try {
    const payload = buildCheckoutApprovedPayload({
      financialProductKey: metadata.financialProductKey,
      orderId: metadata.orderId,
      buyerEmail: metadata.buyerEmail,
      // Slice 1 is platform-direct and anonymous-safe: no authenticated subject.
      buyerIdentitySub: null,
      // Deterministic per event: the Stripe event creation time, so a replayed
      // event id maps to a byte-identical payload the ingress dedups.
      occurredAtIso: new Date(event.created * 1_000).toISOString()
    });
    payloadBuffer = serializeCheckoutPayload(payload);
  } catch (error) {
    // Permanently-invalid metadata can never succeed on retry — return 4xx so
    // Stripe stops redelivering (a 5xx would retry for days and page ops).
    if (error instanceof SelfEmitPayloadError) return jsonError("checkout_webhook_payload_invalid", 400);
    throw error;
  }

  const eventId = selfEmitEventId(metadata.orderId);
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
    // 409 = event id already ingested (idempotent replay of the same order) —
    // the purchase was already fulfilled; treat as success so Stripe stops
    // retrying.
    if (delivered.status === 409) {
      return NextResponse.json({ status: "replayed" }, { status: 200, headers: noStore() });
    }
    if (delivered.status === 404) return jsonError("order_not_found", 404);
    return jsonError("checkout_webhook_rejected", 503);
  } catch {
    return jsonError("checkout_webhook_unavailable", 503);
  }
}

interface CheckoutMetadata {
  financialProductKey: string;
  orderId: string;
  buyerEmail: string;
}

// The three fields create-session wrote into session.metadata. Presence/type
// only — buildCheckoutApprovedPayload re-validates charset, e-mail and length
// at the outbound boundary.
function readCheckoutMetadata(metadata: Stripe.Metadata | null): CheckoutMetadata | null {
  if (!metadata) return null;
  const financialProductKey = metadata.financial_product_key;
  const orderId = metadata.order_id;
  const buyerEmail = metadata.buyer_email;
  if (typeof financialProductKey !== "string" || financialProductKey.length === 0) return null;
  if (typeof orderId !== "string" || orderId.length === 0) return null;
  if (typeof buyerEmail !== "string" || buyerEmail.length === 0) return null;
  return { financialProductKey, orderId, buyerEmail };
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
