import { NextRequest, NextResponse } from "next/server";
import type Stripe from "stripe";

import { proxyCheckoutWebhook } from "@/lib/checkout-webhook-proxy.server";
import { RawBodyTooLargeError, readLimitedRawBody } from "@/lib/limited-raw-body.server";
import {
  type StripeKeyMode,
  StripeConfigError,
  stripeClient,
  stripeKeyMode,
  stripeWebhookSecret
} from "@/lib/stripe-config.server";
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

// Every event type that can carry a FULLY PAID checkout session.
//
// `checkout.session.completed` is the only one card payments produce. Delayed
// notification methods (boleto, Pix, SEPA debit, …) complete the session with
// payment_status "unpaid" and confirm the funds hours-to-days later with
// `checkout.session.async_payment_succeeded` — handling only the first event
// would take the buyer's money and never grant access. create-session pins
// payment_method_types to card today; this set is what makes adding a delayed
// method a one-line, safe change instead of a silent money hole.
//
// `async_payment_failed` is intentionally absent: it is acknowledged as an
// unhandled type, because nothing was granted in the first place.
const PAID_EVENT_TYPES: ReadonlySet<string> = new Set([
  "checkout.session.completed",
  "checkout.session.async_payment_succeeded"
]);

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

  // Mode fence. The signature only proves the sender holds the endpoint secret;
  // it does NOT prove the event belongs to the same Stripe mode as the key this
  // deployment charges with. Without this check a test-mode event delivered to
  // the live URL (a test endpoint mistakenly pointed here, or a replay from the
  // Dashboard's test view) would grant a real course for a fake payment.
  let keyMode: StripeKeyMode;
  try {
    keyMode = stripeKeyMode();
  } catch {
    return jsonError("stripe_not_configured", 503);
  }
  if (event.livemode !== (keyMode === "live")) {
    // Permanent by nature — retrying delivers the same mismatched event.
    return NextResponse.json({ received: true, ignored: "livemode_mismatch" }, { status: 200, headers: noStore() });
  }

  // Acknowledge everything else with 200 so Stripe stops retrying events we
  // intentionally do not act on.
  if (!PAID_EVENT_TYPES.has(event.type)) {
    return NextResponse.json({ received: true }, { status: 200, headers: noStore() });
  }

  const session = event.data.object as Stripe.Checkout.Session;
  // Only a fully paid session grants access. `complete`/`open` sessions or
  // `unpaid`/`no_payment_required` are acknowledged without emitting.
  if (session.payment_status !== "paid") {
    return NextResponse.json({ received: true, ignored: "not_paid" }, { status: 200, headers: noStore() });
  }

  // No usable metadata means the session was not created by
  // app/api/checkout/stripe/create-session (a Dashboard-made Payment Link, a
  // legacy session, another integration on the same account). Nothing to grant,
  // and no retry can change it — 200 so Stripe stops redelivering. Stripe
  // retries EVERY non-2xx for ~3 days, so a 4xx here would have bought noise
  // without buying a fix.
  const metadata = readCheckoutMetadata(session.metadata);
  if (!metadata) {
    return NextResponse.json({ received: true, ignored: "unrecognized_session" }, { status: 200, headers: noStore() });
  }

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
    // Our own metadata failed the outbound contract. Deterministic in the event
    // payload, so every redelivery fails identically: acknowledge with 200
    // instead of burning three days of Stripe retries on a payload that can
    // never be accepted. The real defense is upstream — create-session now
    // enforces the same buyer-email contract (lib/buyer-email.ts) BEFORE the
    // charge exists, which is the only e-mail-shaped way to reach this branch.
    if (error instanceof SelfEmitPayloadError) {
      return NextResponse.json({ received: true, ignored: "payload_invalid" }, { status: 200, headers: noStore() });
    }
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
