import Stripe from "stripe";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

// #148: the webhook must check HOW MUCH was paid, not only that the session is
// "paid". Unlike tests/stripe-webhook.test.ts, the signature here is real: the
// Stripe SDK signs a test payload and the route verifies it, so the amounts
// asserted are the ones that reached constructEvent byte for byte.
const WEBHOOK_SECRET = "whsec_test_amount_check";

const { proxyCheckoutWebhook } = vi.hoisted(() => ({ proxyCheckoutWebhook: vi.fn() }));

vi.mock("@/lib/stripe-config.server", () => ({
  StripeConfigError: class extends Error {},
  stripeClient: () => new Stripe("sk_test_amount_check"),
  stripeKeyMode: () => "test",
  stripeWebhookSecret: () => WEBHOOK_SECRET
}));

vi.mock("@/lib/checkout-webhook-proxy.server", () => ({ proxyCheckoutWebhook }));

import { POST } from "../app/api/webhooks/stripe/route";

const ENDPOINT_KEY = "endpoint_0123456789abcdefghijklmnopqrstuv";
const EVENT_CREATED = 1_753_000_000;
const originalEnvironment = { ...process.env };

interface SessionOverrides {
  eventId?: string;
  amountTotal?: number | null;
  amountSubtotal?: number | null;
  currency?: string | null;
  productKey?: string;
  orderId?: string;
}

// Shape of what create-session produces today: one BRL line item at the
// storefront price, no discount, so amount_total === amount_subtotal.
function paidSession(overrides: SessionOverrides = {}) {
  return {
    id: overrides.eventId ?? "evt_amount_1",
    object: "event",
    type: "checkout.session.completed",
    created: EVENT_CREATED,
    livemode: false,
    data: {
      object: {
        id: "cs_test_amount_1",
        object: "checkout.session",
        payment_status: "paid",
        amount_subtotal: overrides.amountSubtotal === undefined ? 4_700 : overrides.amountSubtotal,
        amount_total: overrides.amountTotal === undefined ? 4_700 : overrides.amountTotal,
        currency: overrides.currency === undefined ? "brl" : overrides.currency,
        metadata: {
          financial_product_key: overrides.productKey ?? "prod_hub2you_condicoes",
          order_id: overrides.orderId ?? "order-amount-1",
          buyer_email: "buyer@example.test",
          buyer_identity_sub: "cognito-sub-amount"
        }
      }
    }
  };
}

function signedRequest(event: unknown, secret = WEBHOOK_SECRET): NextRequest {
  const payload = JSON.stringify(event);
  const signature = Stripe.webhooks.generateTestHeaderString({ payload, secret });
  return new Request("http://localhost:3000/api/webhooks/stripe", {
    method: "POST",
    headers: { "content-type": "application/json", "stripe-signature": signature },
    body: payload
  }) as unknown as NextRequest;
}

function emittedPayloads() {
  return proxyCheckoutWebhook.mock.calls.map(([, body]) => JSON.parse((body as Buffer).toString("utf8")));
}

describe("POST /api/webhooks/stripe — amount check (#148)", () => {
  beforeEach(() => {
    process.env.APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY = ENDPOINT_KEY;
    process.env.APPSELL_CHECKOUT_WEBHOOK_SECRET_B64 = Buffer.alloc(32, 7).toString("base64");
    proxyCheckoutWebhook.mockReset();
    proxyCheckoutWebhook.mockResolvedValue({ status: 202, payload: { status: "accepted" } });
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });

  afterEach(() => {
    process.env = { ...originalEnvironment };
    vi.restoreAllMocks();
  });

  it("grants a full-price purchase exactly as before", async () => {
    const response = await POST(signedRequest(paidSession()));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "accepted" });
    expect(emittedPayloads()).toEqual([{
      event_type: "purchase.approved",
      external_product_id: "prod_hub2you_condicoes",
      external_purchase_id: "order-amount-1",
      occurred_at: new Date(EVENT_CREATED * 1_000).toISOString(),
      buyer_email: "buyer@example.test",
      buyer_identity_sub: "cognito-sub-amount"
    }]);
    expect(proxyCheckoutWebhook.mock.calls[0]![2].eventId).toBe("selfemit:order-amount-1");
  });

  it("refuses access when a discount brought the total below the price", async () => {
    const response = await POST(signedRequest(paidSession({ amountTotal: 0 })));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      received: true,
      ignored: "amount_mismatch",
      order_id: "order-amount-1"
    });
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
    expect(console.error).toHaveBeenCalledWith(expect.stringContaining("\"reason\":\"amount_mismatch\""));
  });

  it("refuses access for a partial discount too", async () => {
    await POST(signedRequest(paidSession({ amountTotal: 470 })));
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  });

  it("refuses access when the session was charged in another currency", async () => {
    const response = await POST(signedRequest(paidSession({ currency: "usd" })));

    expect(await response.json()).toMatchObject({ ignored: "amount_mismatch" });
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  });

  it("refuses access when the amounts are missing from the session", async () => {
    await POST(signedRequest(paidSession({ amountTotal: null, amountSubtotal: null })));
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  });

  it("answers a replayed mismatched event the same way, without emitting", async () => {
    const event = paidSession({ amountTotal: 0 });
    const first = await (await POST(signedRequest(event))).json();
    const replay = await (await POST(signedRequest(event))).json();

    expect(replay).toEqual(first);
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  });

  it("judges each store's session by its own price", async () => {
    await POST(signedRequest(paidSession({ amountTotal: 0 })));
    await POST(signedRequest(paidSession({
      eventId: "evt_amount_other_store",
      amountTotal: 9_900,
      amountSubtotal: 9_900,
      productKey: "prod_outra_loja",
      orderId: "order-other-store"
    })));

    expect(emittedPayloads().map((payload) => payload.external_purchase_id)).toEqual(["order-other-store"]);
    expect(emittedPayloads()[0].external_product_id).toBe("prod_outra_loja");
  });

  it("still rejects an event that is not signed with the endpoint secret", async () => {
    const response = await POST(signedRequest(paidSession(), "whsec_forged"));

    expect(response.status).toBe(400);
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  });
});
