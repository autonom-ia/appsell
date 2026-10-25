import Stripe from "stripe";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

// #148: the webhook must check HOW MUCH was paid against the offer's
// server-side price, not only that the session is "paid". Unlike
// tests/stripe-webhook.test.ts, the signature here is real: the Stripe SDK
// signs a test payload and the route verifies it, so the amounts asserted are
// the ones that reached constructEvent byte for byte.
const WEBHOOK_SECRET = "whsec_test_amount_check";

const { proxyCheckoutWebhook, readPublicStorefront, DomainApiError } = vi.hoisted(() => {
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return { proxyCheckoutWebhook: vi.fn(), readPublicStorefront: vi.fn(), DomainApiError };
});

vi.mock("@/lib/stripe-config.server", () => ({
  StripeConfigError: class extends Error {},
  stripeClient: () => new Stripe("sk_test_amount_check"),
  stripeKeyMode: () => "test",
  stripeWebhookSecret: () => WEBHOOK_SECRET
}));

vi.mock("@/lib/checkout-webhook-proxy.server", () => ({ proxyCheckoutWebhook }));
vi.mock("@/lib/internal-api-client.server", () => ({ readPublicStorefront, DomainApiError }));

import { POST } from "../app/api/webhooks/stripe/route";

const ENDPOINT_KEY = "endpoint_0123456789abcdefghijklmnopqrstuv";
const EVENT_CREATED = 1_753_000_000;
const HUB2YOU_PRICE = 4_700;
const OTHER_STORE_PRICE = 19_900;
const originalEnvironment = { ...process.env };

// Two stores, each with one product at its own server-side price.
const STOREFRONTS: Record<string, unknown> = {
  hub2you: storefront("hub2you", "11111111-1111-4111-8111-111111111111", "condicoes-gerais", HUB2YOU_PRICE),
  "outra-loja": storefront("outra-loja", "44444444-4444-4444-8444-444444444444", "curso", OTHER_STORE_PRICE)
};

function storefront(slug: string, sellerId: string, productSlug: string, unitAmountMinor: number) {
  return {
    schema_version: 1,
    seller: { id: sellerId, slug, name: slug },
    products: [{
      id: "22222222-2222-4222-8222-222222222222",
      slug: productSlug,
      title: "Produto",
      description: "Produto",
      publication_id: "33333333-3333-4333-8333-333333333333",
      publication_version: 1,
      price: { unit_amount_minor: unitAmountMinor, currency: "BRL", billing_period: "one_time" }
    }]
  };
}

interface SessionOverrides {
  eventId?: string;
  amountTotal?: number | null;
  amountSubtotal?: number | null;
  currency?: string | null;
  productKey?: string;
  orderId?: string;
}

// Shape of what create-session produces today for the Hub2You product: one BRL
// line item at the storefront price, no discount.
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
        amount_subtotal: overrides.amountSubtotal === undefined ? HUB2YOU_PRICE : overrides.amountSubtotal,
        amount_total: overrides.amountTotal === undefined ? HUB2YOU_PRICE : overrides.amountTotal,
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

function loggedLines(): Array<Record<string, unknown>> {
  return vi.mocked(console.error).mock.calls.map(([line]) => JSON.parse(String(line)) as Record<string, unknown>);
}

// A cause no redelivery fixes: 200 so Stripe stops, and one durable line.
async function expectPermanentDrop(reason: string) {
  const response = await POST(signedRequest(paidSession()));
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ received: true, ignored: reason, order_id: "order-amount-1" });
  expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  expect(loggedLines()).toEqual([expect.objectContaining({
    level: "error",
    marker: "appsell.stripe_webhook.dropped",
    reason,
    stripe_event_id: "evt_amount_1",
    order_id: "order-amount-1"
  })]);
}

// An outage: 503 so Stripe keeps redelivering, and the cause on record while
// the retry window is still open.
async function expectRetrying(error: string, reason: string, extra: Record<string, unknown> = {}) {
  const response = await POST(signedRequest(paidSession()));
  expect(response.status).toBe(503);
  expect(await response.json()).toEqual({ error });
  expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  const logged = loggedLines();
  expect(logged).toEqual([expect.objectContaining({
    level: "error",
    marker: "appsell.stripe_webhook.retrying",
    reason,
    stripe_event_id: "evt_amount_1",
    order_id: "order-amount-1",
    ...extra
  })]);
  expect(JSON.stringify(logged)).not.toContain("buyer@example.test");
  expect(JSON.stringify(logged)).not.toContain("cognito-sub-amount");
}

async function expectAmountMismatch(event: ReturnType<typeof paidSession>, orderId = "order-amount-1") {
  const response = await POST(signedRequest(event));
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ received: true, ignored: "amount_mismatch", order_id: orderId });
  expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  const logged = vi.mocked(console.error).mock.calls.map(([line]) => JSON.parse(String(line)));
  expect(logged).toContainEqual(expect.objectContaining({
    level: "error",
    marker: "appsell.stripe_webhook.dropped",
    reason: "amount_mismatch",
    order_id: orderId
  }));
  // Correlation only: never the buyer's e-mail or subject.
  expect(JSON.stringify(logged)).not.toContain("buyer@example.test");
  expect(JSON.stringify(logged)).not.toContain("cognito-sub-amount");
}

describe("POST /api/webhooks/stripe — amount check (#148)", () => {
  beforeEach(() => {
    process.env.APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY = ENDPOINT_KEY;
    process.env.APPSELL_CHECKOUT_WEBHOOK_SECRET_B64 = Buffer.alloc(32, 7).toString("base64");
    process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = JSON.stringify({
      "hub2you/condicoes-gerais": { financial_product_key: "prod_hub2you_condicoes" },
      "outra-loja/curso": { financial_product_key: "prod_outra_loja" }
    });
    proxyCheckoutWebhook.mockReset();
    proxyCheckoutWebhook.mockResolvedValue({ status: 202, payload: { status: "accepted" } });
    readPublicStorefront.mockReset();
    readPublicStorefront.mockImplementation(async (slug: string) => STOREFRONTS[slug]);
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
    expect(readPublicStorefront).toHaveBeenCalledWith("hub2you");
  });

  it("refuses a R$0,50 session carrying valid metadata of a R$199 product", async () => {
    // Probe P4 from the review: total === subtotal, so only the server-side
    // price can tell this session was not made by create-session.
    await expectAmountMismatch(paidSession({
      amountTotal: 50,
      amountSubtotal: 50,
      productKey: "prod_outra_loja"
    }));
  });

  it("refuses a 100% discount", async () => {
    await expectAmountMismatch(paidSession({ amountTotal: 0 }));
  });

  it("refuses a partial discount", async () => {
    await expectAmountMismatch(paidSession({ amountTotal: 470 }));
  });

  it("refuses a total above the subtotal", async () => {
    await expectAmountMismatch(paidSession({ amountTotal: HUB2YOU_PRICE + 100 }));
  });

  it("refuses a session charged in another currency", async () => {
    await expectAmountMismatch(paidSession({ currency: "usd" }));
  });

  it("refuses a session without amount_total", async () => {
    await expectAmountMismatch(paidSession({ amountTotal: null }));
  });

  it("refuses a session without any amounts", async () => {
    await expectAmountMismatch(paidSession({ amountTotal: null, amountSubtotal: null }));
  });

  it("refuses store A's product paid at store B's price, and grants store B at its own price", async () => {
    await expectAmountMismatch(paidSession({ amountTotal: OTHER_STORE_PRICE, amountSubtotal: OTHER_STORE_PRICE }));

    const response = await POST(signedRequest(paidSession({
      eventId: "evt_amount_other_store",
      amountTotal: OTHER_STORE_PRICE,
      amountSubtotal: OTHER_STORE_PRICE,
      productKey: "prod_outra_loja",
      orderId: "order-other-store"
    })));

    expect(await response.json()).toEqual({ status: "accepted" });
    expect(emittedPayloads().map((payload) => [payload.external_product_id, payload.external_purchase_id]))
      .toEqual([["prod_outra_loja", "order-other-store"]]);
    expect(readPublicStorefront).toHaveBeenLastCalledWith("outra-loja");
  });

  it("refuses a product that is no longer offered on the storefront", async () => {
    readPublicStorefront.mockImplementation(async () => ({ ...STOREFRONTS.hub2you as object, products: [] }));
    await expectAmountMismatch(paidSession());
  });

  it("refuses an offer that is not a one-time BRL price, even at the same number", async () => {
    const hub2you = STOREFRONTS.hub2you as { products: Array<{ price: Record<string, unknown> }> };
    for (const price of [
      { unit_amount_minor: HUB2YOU_PRICE, currency: "BRL", billing_period: "monthly" },
      { unit_amount_minor: HUB2YOU_PRICE, currency: "USD", billing_period: "one_time" }
    ]) {
      readPublicStorefront.mockResolvedValue({ ...hub2you, products: [{ ...hub2you.products[0], price }] });
      vi.mocked(console.error).mockClear();
      await expectAmountMismatch(paidSession());
    }
  });

  it("answers a replayed mismatched event the same way, without emitting", async () => {
    const event = paidSession({ amountTotal: 0 });
    const first = await (await POST(signedRequest(event))).json();
    const replay = await (await POST(signedRequest(event))).json();

    expect(replay).toEqual(first);
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
  });

  it("re-emits a replayed valid event byte for byte, so the ingress dedups it", async () => {
    proxyCheckoutWebhook
      .mockResolvedValueOnce({ status: 202, payload: { status: "accepted" } })
      .mockResolvedValueOnce({ status: 202, payload: { status: "replayed" } });
    const event = paidSession();

    const first = await (await POST(signedRequest(event))).json();
    const replay = await (await POST(signedRequest(event))).json();

    expect([first, replay]).toEqual([{ status: "accepted" }, { status: "replayed" }]);
    const [firstCall, replayCall] = proxyCheckoutWebhook.mock.calls;
    expect((replayCall![1] as Buffer).equals(firstCall![1] as Buffer)).toBe(true);
    expect(replayCall![2].eventId).toBe(firstCall![2].eventId);
  });

  it("drops, with a record, a product that is not in the map", async () => {
    process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = JSON.stringify({
      "outra-loja/curso": { financial_product_key: "prod_outra_loja" }
    });
    await expectPermanentDrop("product_not_mapped");
    expect(readPublicStorefront).not.toHaveBeenCalled();
  });

  it("drops, with a record, a key mapped to two products", async () => {
    process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = JSON.stringify({
      "hub2you/condicoes-gerais": { financial_product_key: "prod_hub2you_condicoes" },
      "outra-loja/curso": { financial_product_key: "prod_hub2you_condicoes" }
    });
    await expectPermanentDrop("product_key_ambiguous");
  });

  it("drops, with a record, a store the domain no longer knows", async () => {
    readPublicStorefront.mockRejectedValue(new DomainApiError(404, "seller_not_found"));
    await expectPermanentDrop("seller_not_found");
  });

  it("keeps Stripe redelivering, with a record, while the map does not parse", async () => {
    process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = "{not json";
    await expectRetrying("product_map_invalid", "product_map_invalid", { error_name: null });
    expect(readPublicStorefront).not.toHaveBeenCalled();
  });

  it("keeps Stripe redelivering, with a record, while the storefront is down", async () => {
    readPublicStorefront.mockRejectedValue(new DomainApiError(503, "domain_api_unavailable"));
    await expectRetrying("amount_verification_unavailable", "storefront_unavailable", {
      error_name: "DomainApiError",
      upstream_status: 503
    });
  });

  it("keeps Stripe redelivering, with a record, on an unexpected storefront failure", async () => {
    readPublicStorefront.mockRejectedValue(new TypeError("fetch failed https://api.internal/secret-path"));
    await expectRetrying("amount_verification_unavailable", "storefront_unavailable", {
      error_name: "TypeError",
      upstream_status: null
    });
    // The name, never the message: it can carry a URL.
    expect(JSON.stringify(loggedLines())).not.toContain("secret-path");
  });

  it("keeps Stripe redelivering, with a record, on a storefront shape this build cannot read", async () => {
    readPublicStorefront.mockResolvedValue({ unexpected: true });
    await expectRetrying("amount_verification_unavailable", "storefront_unreadable");
  });

  it("delivers once the storefront is back, without a duplicate", async () => {
    readPublicStorefront.mockRejectedValueOnce(new DomainApiError(503, "domain_api_unavailable"));
    const down = await POST(signedRequest(paidSession()));
    const back = await POST(signedRequest(paidSession()));

    expect([down.status, back.status]).toEqual([503, 200]);
    expect(await back.json()).toEqual({ status: "accepted" });
    expect(proxyCheckoutWebhook).toHaveBeenCalledOnce();
  });

  it("records why the domain ingress was unreachable", async () => {
    proxyCheckoutWebhook.mockRejectedValue(new Error("socket hang up"));
    const response = await POST(signedRequest(paidSession()));

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: "checkout_webhook_unavailable" });
    expect(loggedLines()).toEqual([expect.objectContaining({
      marker: "appsell.stripe_webhook.retrying",
      reason: "checkout_webhook_unavailable",
      order_id: "order-amount-1",
      error_name: "Error"
    })]);
  });

  it("still rejects an event that is not signed with the endpoint secret", async () => {
    const response = await POST(signedRequest(paidSession(), "whsec_forged"));

    expect(response.status).toBe(400);
    expect(proxyCheckoutWebhook).not.toHaveBeenCalled();
    expect(readPublicStorefront).not.toHaveBeenCalled();
  });
});
