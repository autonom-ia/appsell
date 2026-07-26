import { NextRequest, NextResponse } from "next/server";
import type Stripe from "stripe";

import { DomainApiError, syncPayoutAccountStatus } from "@/lib/internal-api-client.server";
import { RawBodyTooLargeError, readLimitedRawBody } from "@/lib/limited-raw-body.server";
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

// Same deliberate exception as the checkout webhook: branches that answer 2xx
// stop Stripe from ever redelivering, and this app has no logger — one stderr
// line is what lets reconciliation find what was dropped. Never logs personal
// data; account ids are Stripe's own.
function logIrreversibleDrop(input: { reason: string; event: Stripe.Event }) {
  console.error(
    JSON.stringify({
      marker: "appsell.stripe_connect_webhook.dropped",
      reason: input.reason,
      stripe_event_id: input.event.id,
      stripe_event_type: input.event.type,
      livemode: input.event.livemode
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

function noStore() {
  return { "cache-control": "private, no-store" };
}

function jsonError(error: string, status: number) {
  return NextResponse.json({ error }, { status, headers: noStore() });
}
