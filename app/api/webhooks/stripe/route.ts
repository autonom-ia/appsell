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
// that grants course access. `buyer_identity_sub` carries the subject that
// create-session resolved from the buyer's own session, so the entitlement lands
// on their real login rather than on the worker-derived registration:<email>.
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

// Deliberate exception to this repo's no-console rule, and the reason is
// specific: the branches below answer 2xx, which makes Stripe stop redelivering
// FOREVER. Without a line here a lost sale leaves no trace anywhere — Stripe
// files a 2xx as success and this app has no logger. One stderr line is the
// minimum that makes the Stripe ↔ buyer_entitlement reconciliation (#147) able
// to find what it must repair.
//
// Never logs buyer_email or buyer_identity_sub: the point is correlation, not
// personal data. Ids are Stripe's own and appear in the Dashboard anyway.
function logIrreversibleDrop(input: {
  reason: string;
  event: Stripe.Event;
  orderId: string | null;
}) {
  console.error(
    JSON.stringify({
      marker: "appsell.stripe_webhook.dropped",
      reason: input.reason,
      stripe_event_id: input.event.id,
      stripe_event_type: input.event.type,
      livemode: input.event.livemode,
      order_id: input.orderId
    })
  );
}

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
    // ASYMMETRIC ON PURPOSE, because only one direction involves real money.
    //
    // A LIVE event on a test-mode deployment means a buyer really paid and this
    // process cannot fulfil it — typically a half-finished credential swap
    // (sk_test_ already in place, the live whsec_ still registered, so the
    // signature verifies). Answering 2xx here would make Stripe stop retrying
    // and the sale would be lost for good. 5xx keeps it redelivering for ~3
    // days, so restoring the live key still grants access.
    if (event.livemode) {
      return jsonError("livemode_mismatch_live_event", 503);
    }
    // A TEST event on a live deployment moved no real money (a test endpoint
    // pointed here, a Dashboard replay from the test view). Dropping it is
    // correct and permanent, so 2xx to stop the retries.
    logIrreversibleDrop({ reason: "livemode_mismatch_test_event", event, orderId: null });
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
    logIrreversibleDrop({ reason: "unrecognized_session", event, orderId: null });
    return NextResponse.json({ received: true, ignored: "unrecognized_session" }, { status: 200, headers: noStore() });
  }

  // HOW MUCH was paid, not only THAT it was paid (#148). create-session writes
  // one BRL line item at the storefront price and offers no promotion codes, so
  // a session it made always has amount_total === amount_subtotal. A total
  // below the subtotal means a discount (a 100% coupon still reports "paid"),
  // and nothing in this slice prices a discount, so it grants nothing. A
  // different currency or missing amounts are never one of our sessions.
  //
  // Deterministic in the event, so 200: a redelivery would fail identically.
  // Logged so the Stripe ↔ buyer_entitlement reconciliation (#147) sees it.
  // A legitimate discount (coupons) must change THIS rule, not bypass it.
  if (!isFullPriceBrlSession(session)) {
    logIrreversibleDrop({ reason: "amount_mismatch", event, orderId: metadata.orderId });
    return NextResponse.json(
      { received: true, ignored: "amount_mismatch", order_id: metadata.orderId },
      { status: 200, headers: noStore() }
    );
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
      // The subject create-session resolved from the buyer's own session, or
      // null when they checked out anonymously. Forwarding it is what puts the
      // entitlement on the buyer's real login instead of the synthetic
      // registration:<e-mail> identity the worker would otherwise derive.
      buyerIdentitySub: metadata.buyerIdentitySub,
      // Deterministic per event: the Stripe event creation time, so a replayed
      // event id maps to a byte-identical payload the ingress dedups.
      occurredAtIso: new Date(event.created * 1_000).toISOString()
    });
    payloadBuffer = serializeCheckoutPayload(payload);
  } catch (error) {
    // Our own metadata failed the outbound contract. Deterministic in the event
    // payload, so every redelivery fails identically: acknowledge with 200
    // instead of burning three days of Stripe retries on a payload that can
    // never be accepted.
    //
    // KNOWN LIMIT, tracked in #147: a paid session dies here with no durable
    // record on our side — this repo has no logger in app/api, and Stripe files
    // a 2xx as success. Still reachable despite the upstream gate, e.g. a
    // session created before that gate existed, or metadata edited in the
    // Dashboard. The order id is echoed so it is at least recoverable from the
    // Stripe event, and the Stripe ↔ buyer_entitlement reconciliation in #147 is
    // what turns this from silent into detected.
    if (error instanceof SelfEmitPayloadError) {
      logIrreversibleDrop({ reason: "payload_invalid", event, orderId: metadata.orderId });
      return NextResponse.json(
        { received: true, ignored: "payload_invalid", order_id: metadata.orderId },
        { status: 200, headers: noStore() }
      );
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
    // 409 is NOT a replay. Migration 0020 distinguishes the two cases: an
    // identical payload under the same event id returns 202 "replayed" (handled
    // above), while the SAME event id carrying a DIFFERENT payload_sha256
    // returns 409 webhook_event_conflict — meaning this payload was rejected and
    // never ingested. Reachable when two paid events for one order carry
    // different `occurred_at` (checkout.session.completed and
    // async_payment_succeeded have different `created`), when session metadata
    // is edited in the Dashboard, or when the registration path already used
    // `selfemit:<order_id>` for the same order.
    //
    // Retrying cannot resolve a hash conflict, so 2xx is right for Stripe. But
    // it must not be reported as a replay: whether access exists depends on
    // whether the FIRST payload was fulfilled, which this route cannot see.
    // Surfaced under its own marker for the Stripe ↔ buyer_entitlement
    // reconciliation (Refs #147), which is what closes this properly.
    if (delivered.status === 409) {
      logIrreversibleDrop({ reason: "event_id_payload_mismatch", event, orderId: metadata.orderId });
      return NextResponse.json(
        { received: true, conflict: "event_id_payload_mismatch", order_id: metadata.orderId },
        { status: 200, headers: noStore() }
      );
    }
    // The proxy does not look up an order, so a 404 is an integration fault
    // (wrong domain API version, endpoint key not provisioned, bad base URL) —
    // never "this purchase does not exist". Non-2xx on purpose: Stripe keeps
    // redelivering, so fixing the configuration within the retry window still
    // grants access.
    if (delivered.status === 404) return jsonError("checkout_webhook_endpoint_not_found", 502);
    return jsonError("checkout_webhook_rejected", 503);
  } catch {
    return jsonError("checkout_webhook_unavailable", 503);
  }
}

interface CheckoutMetadata {
  financialProductKey: string;
  orderId: string;
  buyerEmail: string;
  buyerIdentitySub: string | null;
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
  // Written by create-session as a string ("" = anonymous, since Stripe
  // metadata cannot hold null). A session created BEFORE this field existed has
  // it absent, which is also anonymous — never a reason to drop a paid event.
  const rawIdentitySub = metadata.buyer_identity_sub;
  if (rawIdentitySub !== undefined && typeof rawIdentitySub !== "string") return null;
  const buyerIdentitySub = rawIdentitySub && rawIdentitySub.length > 0 ? rawIdentitySub : null;
  // buildCheckoutApprovedPayload rejects the worker-reserved `registration:`
  // namespace, and the domain re-validates charset and length independently.
  return { financialProductKey, orderId, buyerEmail, buyerIdentitySub };
}

function isFullPriceBrlSession(session: Stripe.Checkout.Session): boolean {
  const total = session.amount_total;
  return session.currency === "brl" &&
    Number.isInteger(total) &&
    total !== null &&
    total > 0 &&
    total === session.amount_subtotal;
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
