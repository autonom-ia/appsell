import { NextRequest, NextResponse } from "next/server";
import type Stripe from "stripe";

import { findCheckoutProductByKey } from "@/lib/checkout-config";
import { proxyCheckoutWebhook } from "@/lib/checkout-webhook-proxy.server";
import {
  DomainApiError,
  readPublicStorefront,
  resolveCheckoutPayoutDestination,
  syncPayoutAccountStatus
} from "@/lib/internal-api-client.server";
import { parsePublicStorefront } from "@/lib/public-storefront";
import { RawBodyTooLargeError, readLimitedRawBody } from "@/lib/limited-raw-body.server";
import {
  SelfEmitPayloadError,
  buildCheckoutApprovedPayload,
  selfEmitEventId,
  selfEmitWebhookConfig,
  serializeCheckoutPayload,
  signSelfEmitWebhook
} from "@/lib/webhook-self-emit.server";
import {
  type StripeKeyMode,
  StripeConfigError,
  stripeClient,
  stripeConnectWebhookSecret,
  stripeKeyMode
} from "@/lib/stripe-config.server";

// Connect webhook: `account.updated` for the producers' connected accounts.
// The ONLY thing this route does is refresh the status CACHE — the columns the
// /payouts page reads. It cannot bind, re-point, or unbind an account: the
// sync path resolves the seller from the existing unique binding and the
// database refuses everything else. Losing an event is therefore recoverable
// (the next account.updated heals the cache), which shapes the retry policy
// below: transient failures ask Stripe to redeliver, deterministic ones are
// acknowledged so retries stop.
export const dynamic = "force-dynamic";

const MAX_BODY_BYTES = 64 * 1024;
const SIGNATURE_HEADER = "stripe-signature";
const ACCOUNT_PATTERN = /^acct_[A-Za-z0-9]{1,250}$/;

// Same set and same reasoning as the platform webhook: card settles with
// `completed` already paid; delayed methods confirm later via
// `async_payment_succeeded`. Kept in lockstep with create-session's pinned
// payment_method_types.
const PAID_EVENT_TYPES: ReadonlySet<string> = new Set([
  "checkout.session.completed",
  "checkout.session.async_payment_succeeded"
]);

// Same deliberate exception as the checkout webhook: branches that answer 2xx
// stop Stripe from ever redelivering, and this app has no logger — one stderr
// line is what lets reconciliation find what was dropped. Never logs personal
// data; account ids are Stripe's own.
function logIrreversibleDrop(input: {
  reason: string;
  event: Stripe.Event;
  orderId?: string | null;
}) {
  console.error(
    JSON.stringify({
      marker: "appsell.stripe_connect_webhook.dropped",
      reason: input.reason,
      stripe_event_id: input.event.id,
      stripe_event_type: input.event.type,
      livemode: input.event.livemode,
      order_id: input.orderId ?? null
    })
  );
}

export async function POST(request: NextRequest) {
  let rawBody: Buffer;
  try {
    rawBody = await readLimitedRawBody(request, MAX_BODY_BYTES);
  } catch (error) {
    return error instanceof RawBodyTooLargeError
      ? jsonError("payload_too_large", 413)
      : jsonError("invalid_request", 400);
  }

  const signature = request.headers.get(SIGNATURE_HEADER);
  if (!signature) return jsonError("missing_signature", 400);

  let event: Stripe.Event;
  try {
    const client = stripeClient();
    event = client.webhooks.constructEvent(rawBody, signature, stripeConnectWebhookSecret());
  } catch (error) {
    if (error instanceof StripeConfigError) return jsonError("stripe_not_configured", 503);
    return jsonError("invalid_signature", 400);
  }

  // Mode fence, same asymmetry as the checkout webhook: a LIVE event on a
  // test-keyed deployment is a half-finished credential swap — 5xx keeps
  // Stripe redelivering until the live key is restored, so the status cache
  // still heals. A TEST event on a live deployment is noise; 2xx stops it.
  let keyMode: StripeKeyMode;
  try {
    keyMode = stripeKeyMode();
  } catch {
    return jsonError("stripe_not_configured", 503);
  }
  if (event.livemode !== (keyMode === "live")) {
    if (event.livemode) return jsonError("livemode_mismatch_live_event", 503);
    logIrreversibleDrop({ reason: "livemode_mismatch_test_event", event });
    return NextResponse.json(
      { received: true, ignored: "livemode_mismatch" },
      { status: 200, headers: noStore() }
    );
  }

  // Direct-charge sales complete ON the connected account, so their paid
  // events arrive HERE, not at the platform endpoint. Same bridge, same
  // guards as app/api/webhooks/stripe/route.ts: only a fully paid session
  // with create-session's metadata self-emits the signed purchase.approved
  // that grants access.
  if (PAID_EVENT_TYPES.has(event.type)) {
    return bridgePaidSession(event);
  }

  if (event.type !== "account.updated") {
    return NextResponse.json({ received: true }, { status: 200, headers: noStore() });
  }

  const account = event.data.object as Stripe.Account;
  // Connect events carry the connected account id at the top level; the object
  // repeats it. Both must exist and agree — a mismatch means the event does
  // not describe the account it claims to, and no retry changes that.
  const eventAccount = (event as { account?: unknown }).account;
  if (
    typeof eventAccount !== "string" ||
    !ACCOUNT_PATTERN.test(eventAccount) ||
    account.id !== eventAccount
  ) {
    logIrreversibleDrop({ reason: "account_mismatch", event });
    return NextResponse.json(
      { received: true, ignored: "account_mismatch" },
      { status: 200, headers: noStore() }
    );
  }

  const disabledReason = account.requirements?.disabled_reason ?? null;
  try {
    const result = await syncPayoutAccountStatus({
      provider: "stripe",
      providerAccountId: account.id,
      chargesEnabled: account.charges_enabled === true,
      payoutsEnabled: account.payouts_enabled === true,
      detailsSubmitted: account.details_submitted === true,
      disabledReason:
        typeof disabledReason === "string" && disabledReason.length > 0
          ? disabledReason.slice(0, 255)
          : null
    });
    if (!result.updated) {
      // An account this platform never bound (orphan from a lost bind race,
      // another integration on the same platform account). Retries cannot make
      // it bindable.
      logIrreversibleDrop({ reason: "unknown_account", event });
      return NextResponse.json(
        { received: true, ignored: "unknown_account" },
        { status: 200, headers: noStore() }
      );
    }
    return NextResponse.json({ received: true, updated: true }, { status: 200, headers: noStore() });
  } catch (error) {
    // 422 is deterministic in the event payload — every redelivery fails the
    // same way, so acknowledge and surface it for reconciliation. Everything
    // else (403 misconfigured worker envelope, 5xx, network) is transient or
    // fixable: non-2xx keeps Stripe redelivering while it gets fixed, and the
    // cache heals on the next delivery.
    if (error instanceof DomainApiError && error.status === 422) {
      logIrreversibleDrop({ reason: "sync_rejected_payload", event });
      return NextResponse.json(
        { received: true, ignored: "sync_rejected_payload" },
        { status: 200, headers: noStore() }
      );
    }
    return jsonError("payout_sync_unavailable", 503);
  }
}

// The paid-session → signed self-emit bridge, mirroring
// app/api/webhooks/stripe/route.ts branch by branch (that route's comments
// carry the full reasoning per status code). Deliberately duplicated instead
// of refactoring the deployed platform route in this PR; extracting the shared
// bridge is follow-up work.
async function bridgePaidSession(event: Stripe.Event) {
  const session = event.data.object as Stripe.Checkout.Session;
  if (session.payment_status !== "paid") {
    return NextResponse.json(
      { received: true, ignored: "not_paid" },
      { status: 200, headers: noStore() }
    );
  }

  const metadata = readCheckoutMetadata(session.metadata);
  if (!metadata) {
    logIrreversibleDrop({ reason: "unrecognized_session", event });
    return NextResponse.json(
      { received: true, ignored: "unrecognized_session" },
      { status: 200, headers: noStore() }
    );
  }

  // TRUST BOUNDARY (security-review finding): the session's metadata is only
  // as honest as whoever could mint a session on a connected account. Today
  // producers cannot (stripe_dashboard "none", no API keys of their own), but
  // that guarantee lives in ANOTHER file's configuration — so this route
  // verifies, not assumes:
  //   1. the product key maps back to exactly one seller/product;
  //   2. the event's connected account IS that seller's bound destination —
  //      a session minted on account A can never fulfil seller B's product;
  //   3. the amount actually paid equals the offer's server-side price — a
  //      forged R$0,50 session cannot grant a R$199 course.
  const eventAccount = (event as { account?: unknown }).account;
  if (typeof eventAccount !== "string" || !ACCOUNT_PATTERN.test(eventAccount)) {
    logIrreversibleDrop({ reason: "paid_event_without_account", event, orderId: metadata.orderId });
    return NextResponse.json(
      { received: true, ignored: "paid_event_without_account" },
      { status: 200, headers: noStore() }
    );
  }
  const origin = findCheckoutProductByKey(metadata.financialProductKey);
  if (!origin) {
    // Deploy configuration, fixable within the retry window: non-2xx keeps
    // Stripe redelivering while the map is restored.
    return jsonError("product_not_mapped", 503);
  }
  let ownedAccount: string | null;
  let expectedAmountMinor: number | null;
  try {
    const parsed = parsePublicStorefront(await readPublicStorefront(origin.sellerSlug));
    if (!parsed) return jsonError("fulfillment_verification_unavailable", 503);
    const product = parsed.products.find((candidate) => candidate.slug === origin.productSlug);
    expectedAmountMinor = product?.price?.unit_amount_minor ?? null;
    const destination = await resolveCheckoutPayoutDestination(parsed.seller.id);
    ownedAccount = destination.payout_destination?.provider_account_id ?? null;
  } catch {
    return jsonError("fulfillment_verification_unavailable", 503);
  }
  if (ownedAccount !== eventAccount) {
    logIrreversibleDrop({ reason: "account_product_mismatch", event, orderId: metadata.orderId });
    return NextResponse.json(
      { received: true, ignored: "account_product_mismatch" },
      { status: 200, headers: noStore() }
    );
  }
  if (
    expectedAmountMinor === null ||
    session.amount_total !== expectedAmountMinor ||
    session.currency !== "brl"
  ) {
    logIrreversibleDrop({ reason: "amount_mismatch", event, orderId: metadata.orderId });
    return NextResponse.json(
      { received: true, ignored: "amount_mismatch" },
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
    payloadBuffer = serializeCheckoutPayload(
      buildCheckoutApprovedPayload({
        financialProductKey: metadata.financialProductKey,
        orderId: metadata.orderId,
        buyerEmail: metadata.buyerEmail,
        buyerIdentitySub: metadata.buyerIdentitySub,
        occurredAtIso: new Date(event.created * 1_000).toISOString()
      })
    );
  } catch (error) {
    if (error instanceof SelfEmitPayloadError) {
      logIrreversibleDrop({ reason: "payload_invalid", event });
      return NextResponse.json(
        { received: true, ignored: "payload_invalid", order_id: metadata.orderId },
        { status: 200, headers: noStore() }
      );
    }
    throw error;
  }

  const headers = signSelfEmitWebhook({
    endpointKey: config.endpointKey,
    eventId: selfEmitEventId(metadata.orderId),
    timestampSeconds: Math.floor(Date.now() / 1_000),
    body: payloadBuffer,
    secret: config.secret
  });

  try {
    const delivered = await proxyCheckoutWebhook(config.endpointKey, payloadBuffer, headers);
    if (delivered.status === 202 && isDeliveredRecord(delivered.payload)) {
      const status = delivered.payload.status;
      if (status === "accepted" || status === "replayed") {
        return NextResponse.json({ status }, { status: 200, headers: noStore() });
      }
    }
    if (delivered.status === 409) {
      logIrreversibleDrop({ reason: "event_id_payload_mismatch", event });
      return NextResponse.json(
        { received: true, conflict: "event_id_payload_mismatch", order_id: metadata.orderId },
        { status: 200, headers: noStore() }
      );
    }
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

function readCheckoutMetadata(metadata: Stripe.Metadata | null): CheckoutMetadata | null {
  if (!metadata) return null;
  const financialProductKey = metadata.financial_product_key;
  const orderId = metadata.order_id;
  const buyerEmail = metadata.buyer_email;
  if (typeof financialProductKey !== "string" || financialProductKey.length === 0) return null;
  if (typeof orderId !== "string" || orderId.length === 0) return null;
  if (typeof buyerEmail !== "string" || buyerEmail.length === 0) return null;
  const rawIdentitySub = metadata.buyer_identity_sub;
  if (rawIdentitySub !== undefined && typeof rawIdentitySub !== "string") return null;
  const buyerIdentitySub = rawIdentitySub && rawIdentitySub.length > 0 ? rawIdentitySub : null;
  return { financialProductKey, orderId, buyerEmail, buyerIdentitySub };
}

function isDeliveredRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function noStore() {
  return { "cache-control": "private, no-store" };
}

function jsonError(error: string, status: number) {
  return NextResponse.json({ error }, { status, headers: noStore() });
}
