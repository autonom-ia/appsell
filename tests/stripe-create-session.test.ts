import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

// Hoisted so the (hoisted) vi.mock factories below can reference them.
const {
  sessionsCreate,
  readPublicStorefront,
  resolveCheckoutPayoutDestination,
  createFinancialExternalCheckoutIntent,
  createFinancialExternalCheckoutSession,
  FakeStripeConfigError,
  FakeFinancialConfigError,
  FakeFinancialCheckoutError,
  DomainApiError,
  resolveBuyerIdentityMock
} = vi.hoisted(() => {
  class FakeStripeConfigError extends Error {}
  class FakeFinancialConfigError extends Error {}
  class FakeFinancialCheckoutError extends Error {
    constructor(message: string, readonly status: number) {
      super(message);
    }
  }
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return {
    sessionsCreate: vi.fn(async () => ({ url: "https://checkout.stripe.test/session/cs_test_123" })),
    readPublicStorefront: vi.fn(),
    // The legacy platform-direct suite: no payout destination bound. The
    // direct-charge branch has its own suite.
    resolveCheckoutPayoutDestination: vi.fn<
      (sellerId: string) => Promise<{ payout_destination: { provider_account_id: string; charges_enabled: boolean } | null }>
    >(async () => ({ payout_destination: null })),
    createFinancialExternalCheckoutIntent: vi.fn(async () => ({
      checkoutIntentId: "0c0c0c0c-0c0c-4c0c-8c0c-0c0c0c0c0c0c",
      checkoutUrl: "https://financial.test/financial/public/external-checkout/0c0c0c0c-0c0c-4c0c-8c0c-0c0c0c0c0c0c"
    })),
    createFinancialExternalCheckoutSession: vi.fn(async () => "https://checkout.stripe.com/c/pay/cs_test_123"),
    FakeStripeConfigError,
    FakeFinancialConfigError,
    FakeFinancialCheckoutError,
    DomainApiError,
    resolveBuyerIdentityMock: vi.fn(async () => ({ status: "authenticated", subject: "cognito-sub-default" }) as { status: string; subject?: string })
  };
});

// Fake Stripe client — we assert the session params the route builds, never the
// real network call.
vi.mock("@/lib/stripe-config.server", () => ({
  StripeConfigError: FakeStripeConfigError,
  stripeClient: () => ({ checkout: { sessions: { create: sessionsCreate } } }),
  stripeWebhookSecret: () => "whsec_test"
}));

// Fake domain storefront read — this is the server-side price source; the route
// must use THIS amount, never anything from the request body.
vi.mock("@/lib/internal-api-client.server", () => ({
  readPublicStorefront,
  resolveCheckoutPayoutDestination,
  DomainApiError
}));

// Identity handoff: the resolved subject is what puts the entitlement on the
// buyer's real login instead of the synthetic registration:<e-mail> one.
vi.mock("@/lib/buyer-identity.server", () => ({
  resolveBuyerIdentity: resolveBuyerIdentityMock
}));

vi.mock("@/lib/financial-external-checkout.server", () => ({
  checkoutProvider: () => process.env.APPSELL_CHECKOUT_PROVIDER === "financial_checkout" ? "financial_checkout" : "local_stripe",
  checkoutWebhookEndpointKey: () => process.env.APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY ?? "",
  createFinancialExternalCheckoutIntent,
  createFinancialExternalCheckoutSession,
  FinancialExternalCheckoutConfigError: FakeFinancialConfigError,
  FinancialExternalCheckoutError: FakeFinancialCheckoutError
}));

import { POST } from "../app/api/checkout/stripe/create-session/route";

const ORIGIN = "http://localhost:3000";
const SELLER = "autonomia-demo";
const PRODUCT = "codex-na-pratica";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function storefrontWithPrice(unitAmountMinor: number | null, billingPeriod = "one_time", currency = "BRL") {
  return {
    schema_version: 1,
    seller: {
      id: "11111111-1111-4111-8111-111111111111",
      slug: SELLER,
      name: "Autonomia Demo",
      financial_operator_id: "44444444-4444-4444-8444-444444444444",
      financial_product_catalog_item_id: "55555555-5555-4555-8555-555555555555"
    },
    products: [
      {
        id: "22222222-2222-4222-8222-222222222222",
        slug: PRODUCT,
        title: "Codex na Prática",
        description: "Curso",
        publication_id: "33333333-3333-4333-8333-333333333333",
        publication_version: 1,
        cover: {
          url: "https://storage.test/appsell-ready/cover?X-Amz-Signature=fresh",
          media_type: "image/png",
          width: 1600,
          height: 900,
          expires_in_seconds: 60
        },
        price: unitAmountMinor === null
          ? null
          : { unit_amount_minor: unitAmountMinor, currency, billing_period: billingPeriod }
      }
    ]
  };
}

function request(body: unknown, { origin = ORIGIN }: { origin?: string | null } = {}): NextRequest {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (origin !== null) headers.origin = origin;
  return new Request(`${ORIGIN}/api/checkout/stripe/create-session`, {
    method: "POST",
    headers,
    body: JSON.stringify(body)
  }) as unknown as NextRequest;
}

const validBody = { seller_slug: SELLER, product_slug: PRODUCT, buyer_email: "Buyer@Example.TEST" };
const originalEnvironment = { ...process.env };

describe("POST /api/checkout/stripe/create-session", () => {
  beforeEach(() => {
    process.env.APPSELL_PUBLIC_ORIGIN = ORIGIN;
    process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = JSON.stringify({
      [`${SELLER}/${PRODUCT}`]: { financial_product_key: "prod_codex_v1" }
    });
    readPublicStorefront.mockReset();
    sessionsCreate.mockClear();
    createFinancialExternalCheckoutIntent.mockClear();
    resolveCheckoutPayoutDestination.mockClear();
    resolveBuyerIdentityMock.mockReset();
    resolveBuyerIdentityMock.mockResolvedValue({ status: "authenticated", subject: "cognito-sub-default" });
  });

  afterEach(() => {
    process.env = { ...originalEnvironment };
  });

  it("rejects a cross-origin request (403) before touching Stripe or the domain", async () => {
    const response = await POST(request(validBody, { origin: "https://evil.test" }));
    expect(response.status).toBe(403);
    expect(readPublicStorefront).not.toHaveBeenCalled();
    expect(sessionsCreate).not.toHaveBeenCalled();
  });

  it("rejects missing/invalid input (400)", async () => {
    for (const body of [
      {},
      { seller_slug: SELLER, product_slug: PRODUCT },
      { seller_slug: "Bad Slug", product_slug: PRODUCT, buyer_email: "a@b.co" },
      { seller_slug: SELLER, product_slug: PRODUCT, buyer_email: "not-an-email" },
      { seller_slug: SELLER, product_slug: PRODUCT, buyer_email: "a@b.co", extra: 1 }
    ]) {
      const response = await POST(request(body));
      expect(response.status).toBe(400);
    }
    expect(sessionsCreate).not.toHaveBeenCalled();
  });

  it("rejects an e-mail the domain ingress would refuse, BEFORE creating a charge", async () => {
    // The ingress (webhook_adapters.py::_valid_buyer_email) bounds the address
    // at 6..242 UTF-8 octets and forbids control characters. An address that
    // passes checkout but fails the ingress yields a paid, unfulfillable order,
    // so the same contract runs here — before any money moves.
    readPublicStorefront.mockResolvedValue(storefrontWithPrice(100));
    for (const buyerEmail of [
      `${"a".repeat(240)}@example.test`, // 253 octets: over the 242 bound
      "a@b.c", // 5 octets: under the 6 bound
      "buyer@exämple.test", // non-ASCII: JS/Python case-folding can diverge
      "buyer\u0000@example.test" // NUL is in the ingress' forbidden class
    ]) {
      const response = await POST(request({ seller_slug: SELLER, product_slug: PRODUCT, buyer_email: buyerEmail }));
      expect(response.status).toBe(400);
      expect(await response.json()).toEqual({ error: "invalid_request" });
    }
    expect(sessionsCreate).not.toHaveBeenCalled();
  });

  it("returns 409 when the product is not mapped to a Financial product key", async () => {
    process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = "{}";
    const response = await POST(request(validBody));
    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: "product_not_configured" });
    expect(readPublicStorefront).not.toHaveBeenCalled();
  });

  it("uses Financial hosted checkout with Sell snapshot when the provider flag is enabled", async () => {
    process.env.APPSELL_CHECKOUT_PROVIDER = "financial_checkout";
    process.env.APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY = "endpointkeyendpointkeyendpointkey123456";
    process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = "{}";
    readPublicStorefront.mockResolvedValue(storefrontWithPrice(4990));

    const response = await POST(request(validBody));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      url: "https://checkout.stripe.com/c/pay/cs_test_123"
    });
    expect(sessionsCreate).not.toHaveBeenCalled();
    expect(resolveCheckoutPayoutDestination).not.toHaveBeenCalled();
    expect(createFinancialExternalCheckoutIntent).toHaveBeenCalledWith(expect.objectContaining({
      operatorId: "44444444-4444-4444-8444-444444444444",
      productCatalogItemId: "55555555-5555-4555-8555-555555555555",
      seller: expect.objectContaining({ slug: SELLER }),
      buyer: expect.objectContaining({ userId: "cognito-sub-default", email: "buyer@example.test" }),
      product: expect.objectContaining({
        externalProductId: "22222222-2222-4222-8222-222222222222",
        fulfillmentExternalProductId: "22222222-2222-4222-8222-222222222222",
        title: "Codex na Prática",
        imageUrl: `${ORIGIN}/api/storefronts/${SELLER}/products/${PRODUCT}/cover`
      }),
      pricing: { amountMinor: 4990, currency: "BRL", billingPeriod: "one_time" },
      connect: { accountId: null, applicationFeeAmountMinor: null },
      fulfillmentWebhookUrl: `${ORIGIN}/api/webhooks/checkouts/endpointkeyendpointkeyendpointkey123456`
    }));
    expect(createFinancialExternalCheckoutSession).toHaveBeenCalledWith(
      "https://financial.test/financial/public/external-checkout/0c0c0c0c-0c0c-4c0c-8c0c-0c0c0c0c0c0c"
    );
  });

  it("rejects a Financial checkout when the seller has no Financial binding", async () => {
    process.env.APPSELL_CHECKOUT_PROVIDER = "financial_checkout";
    const storefront = storefrontWithPrice(4990);
    readPublicStorefront.mockResolvedValue({
      ...storefront,
      seller: { id: storefront.seller.id, slug: storefront.seller.slug, name: storefront.seller.name }
    });

    const response = await POST(request(validBody));

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: "seller_financial_not_configured" });
    expect(resolveBuyerIdentityMock).not.toHaveBeenCalled();
    expect(createFinancialExternalCheckoutIntent).not.toHaveBeenCalled();
  });

  it("builds a one-time BRL session with the SERVER-SIDE amount and correct metadata", async () => {
    readPublicStorefront.mockResolvedValue(storefrontWithPrice(4990));
    const response = await POST(request(validBody));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ url: "https://checkout.stripe.test/session/cs_test_123" });

    expect(sessionsCreate).toHaveBeenCalledOnce();
    const params = (sessionsCreate.mock.calls[0] as unknown[])[0] as {
      mode: string;
      payment_method_types: string[];
      line_items: Array<{
        quantity: number;
        price_data: { currency: string; unit_amount: number; product_data: { name: string } };
      }>;
      customer_email: string;
      success_url: string;
      cancel_url: string;
      metadata: Record<string, string>;
    };
    expect(params.mode).toBe("payment");
    // Pinned, not inherited from the Dashboard: a delayed-notification method
    // enabled there would settle via checkout.session.async_payment_succeeded.
    expect(params.payment_method_types).toEqual(["card"]);
    expect(params.line_items).toHaveLength(1);
    expect(params.line_items[0].quantity).toBe(1);
    expect(params.line_items[0].price_data.currency).toBe("brl");
    // Amount comes from the storefront projection (4990), not the request.
    expect(params.line_items[0].price_data.unit_amount).toBe(4990);
    expect(params.line_items[0].price_data.product_data.name).toBe("Codex na Prática");
    expect(params.customer_email).toBe("buyer@example.test");
    expect(params.success_url).toContain(ORIGIN);
    expect(params.cancel_url).toContain(ORIGIN);
    expect(params.metadata.financial_product_key).toBe("prod_codex_v1");
    expect(params.metadata.buyer_email).toBe("buyer@example.test");
    expect(params.metadata.order_id).toMatch(UUID);
    expect(readPublicStorefront).toHaveBeenCalledWith(SELLER);
  });

  it("carries the authenticated buyer subject into session metadata", async () => {
    // Without this the worker derives registration:<e-mail>, a DIFFERENT
    // identity row from the signed-in buyer — they pay and /library stays empty.
    resolveBuyerIdentityMock.mockResolvedValue({ status: "authenticated", subject: "cognito-sub-abc123" });
    readPublicStorefront.mockResolvedValue(storefrontWithPrice(100));
    const response = await POST(request(validBody));
    expect(response.status).toBe(200);
    const params = (sessionsCreate.mock.calls[0] as unknown[])[0] as { metadata: Record<string, string> };
    expect(params.metadata.buyer_identity_sub).toBe("cognito-sub-abc123");
    // Resolved against the e-mail actually being charged, normalized.
    expect(resolveBuyerIdentityMock).toHaveBeenCalledWith(expect.anything(), "buyer@example.test");
  });

  it("refuses to charge an anonymous buyer (401) — no claim path exists", async () => {
    // With no subject the worker binds the grant to registration:<e-mail>, and
    // nothing in this repo lets a real login claim such a grant. Charging would
    // take money for access nobody can reach, so it fails BEFORE Stripe.
    resolveBuyerIdentityMock.mockResolvedValue({ status: "anonymous" });
    readPublicStorefront.mockResolvedValue(storefrontWithPrice(100));
    const response = await POST(request(validBody));
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ error: "authentication_required" });
    expect(sessionsCreate).not.toHaveBeenCalled();
  });

  it("refuses to charge when the session belongs to someone else (409)", async () => {
    resolveBuyerIdentityMock.mockResolvedValue({ status: "mismatch" });
    readPublicStorefront.mockResolvedValue(storefrontWithPrice(100));
    const response = await POST(request(validBody));
    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: "buyer_email_mismatch" });
    expect(sessionsCreate).not.toHaveBeenCalled();
  });

  it("refuses to charge when the session store is unreachable (503), not silently anonymous", async () => {
    // A Redis outage must never be downgraded to "anonymous": that would charge
    // and bind the purchase to an unreachable identity.
    resolveBuyerIdentityMock.mockResolvedValue({ status: "unavailable" });
    readPublicStorefront.mockResolvedValue(storefrontWithPrice(100));
    const response = await POST(request(validBody));
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: "session_unavailable" });
    expect(sessionsCreate).not.toHaveBeenCalled();
  });

  it("returns 409 when the offer has no chargeable one-time price", async () => {
    readPublicStorefront.mockResolvedValue(storefrontWithPrice(null));
    const nullPrice = await POST(request(validBody));
    expect(nullPrice.status).toBe(409);
    expect(await nullPrice.json()).toEqual({ error: "product_not_purchasable" });

    readPublicStorefront.mockResolvedValue(storefrontWithPrice(4990, "monthly"));
    const subscription = await POST(request(validBody));
    expect(subscription.status).toBe(409);
    expect(sessionsCreate).not.toHaveBeenCalled();
  });

  it("maps a 404 from the domain to seller_not_found", async () => {
    readPublicStorefront.mockRejectedValue(new DomainApiError(404, "seller_not_found"));
    const response = await POST(request(validBody));
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: "seller_not_found" });
  });
});

// #440: the store is the seller. Behind APPSELL_FINANCIAL_CONNECT_SELLER_IDS
// (empty by default) the Financial checkout carries the store's own connected
// account and the platform commission. Outside the list the payload must stay
// exactly as it is today.
describe("POST /api/checkout/stripe/create-session — Financial with the store's connected account (#440)", () => {
  const SELLER_ID = "11111111-1111-4111-8111-111111111111";
  const OTHER_SELLER_ID = "66666666-6666-4666-8666-666666666666";
  const ENDPOINT_KEY = "endpointkeyendpointkeyendpointkey123456";

  function readyDestination(accountId = "acct_store_a") {
    return { payout_destination: { provider_account_id: accountId, charges_enabled: true } };
  }

  function intentInput(call = 0) {
    return (createFinancialExternalCheckoutIntent.mock.calls[call] as unknown[])[0] as Record<string, unknown>;
  }

  beforeEach(() => {
    process.env.APPSELL_PUBLIC_ORIGIN = ORIGIN;
    process.env.APPSELL_CHECKOUT_PROVIDER = "financial_checkout";
    process.env.APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY = ENDPOINT_KEY;
    process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = "{}";
    delete process.env.APPSELL_FINANCIAL_CONNECT_SELLER_IDS;
    readPublicStorefront.mockReset();
    readPublicStorefront.mockResolvedValue(storefrontWithPrice(4990));
    createFinancialExternalCheckoutIntent.mockClear();
    createFinancialExternalCheckoutSession.mockClear();
    resolveCheckoutPayoutDestination.mockReset();
    resolveCheckoutPayoutDestination.mockResolvedValue({ payout_destination: null });
    resolveBuyerIdentityMock.mockReset();
    resolveBuyerIdentityMock.mockResolvedValue({ status: "authenticated", subject: "cognito-sub-default" });
  });

  afterEach(() => {
    vi.useRealTimers();
    process.env = { ...originalEnvironment };
  });

  it("outside the list, sends exactly today's payload: no account, no commission, random idempotency key", async () => {
    process.env.APPSELL_FINANCIAL_CONNECT_SELLER_IDS = OTHER_SELLER_ID;

    const response = await POST(request(validBody));

    expect(response.status).toBe(200);
    expect(resolveCheckoutPayoutDestination).not.toHaveBeenCalled();
    expect(intentInput()).toEqual({
      operatorId: "44444444-4444-4444-8444-444444444444",
      productCatalogItemId: "55555555-5555-4555-8555-555555555555",
      seller: { id: SELLER_ID, slug: SELLER, name: "Autonomia Demo" },
      buyer: { userId: "cognito-sub-default", email: "buyer@example.test", identitySub: "cognito-sub-default" },
      product: {
        externalProductId: "22222222-2222-4222-8222-222222222222",
        externalOfferId: "33333333-3333-4333-8333-333333333333",
        fulfillmentExternalProductId: "22222222-2222-4222-8222-222222222222",
        title: "Codex na Prática",
        description: "Curso",
        imageUrl: `${ORIGIN}/api/storefronts/${SELLER}/products/${PRODUCT}/cover`
      },
      pricing: { amountMinor: 4990, currency: "BRL", billingPeriod: "one_time" },
      connect: { accountId: null, applicationFeeAmountMinor: null },
      successUrl: `${ORIGIN}/library?checkout=success`,
      cancelUrl: `${ORIGIN}/s/${SELLER}/p/${PRODUCT}?checkout=cancelled`,
      fulfillmentWebhookUrl: `${ORIGIN}/api/webhooks/checkouts/${ENDPOINT_KEY}`,
      fulfillmentEndpointKey: ENDPOINT_KEY,
      idempotencyKey: expect.stringMatching(UUID)
    });
  });

  it("with the list unset, two identical clicks still get different keys (today's behavior)", async () => {
    await POST(request(validBody));
    await POST(request(validBody));

    expect(resolveCheckoutPayoutDestination).not.toHaveBeenCalled();
    expect(intentInput(0).idempotencyKey).not.toEqual(intentInput(1).idempotencyKey);
  });

  it("on the list, sends the store's own account and the platform commission", async () => {
    process.env.APPSELL_FINANCIAL_CONNECT_SELLER_IDS = ` ${OTHER_SELLER_ID} , ${SELLER_ID} `;
    resolveCheckoutPayoutDestination.mockResolvedValue(readyDestination());

    const response = await POST(request(validBody));

    expect(response.status).toBe(200);
    expect(resolveCheckoutPayoutDestination).toHaveBeenCalledOnce();
    expect(resolveCheckoutPayoutDestination).toHaveBeenCalledWith(SELLER_ID);
    // launch plan: floor(4990 * 5.5%) + 49 = 274 + 49.
    expect(intentInput().connect).toEqual({ accountId: "acct_store_a", applicationFeeAmountMinor: 323 });
    expect(intentInput().pricing).toEqual({ amountMinor: 4990, currency: "BRL", billingPeriod: "one_time" });
  });

  it("never sends another store's account: the destination is resolved for the storefront's own seller", async () => {
    process.env.APPSELL_FINANCIAL_CONNECT_SELLER_IDS = `${SELLER_ID},${OTHER_SELLER_ID}`;
    resolveCheckoutPayoutDestination.mockImplementation(async (sellerId: string) =>
      sellerId === SELLER_ID ? readyDestination("acct_store_a") : readyDestination("acct_store_b"));
    const otherStorefront = storefrontWithPrice(4990);
    readPublicStorefront.mockResolvedValueOnce(storefrontWithPrice(4990)).mockResolvedValueOnce({
      ...otherStorefront,
      seller: { ...otherStorefront.seller, id: OTHER_SELLER_ID }
    });

    await POST(request(validBody));
    await POST(request(validBody));

    expect(resolveCheckoutPayoutDestination.mock.calls).toEqual([[SELLER_ID], [OTHER_SELLER_ID]]);
    expect((intentInput(0).connect as { accountId: string }).accountId).toBe("acct_store_a");
    expect((intentInput(1).connect as { accountId: string }).accountId).toBe("acct_store_b");
  });

  it("refuses to charge (409) when the store on the list has no connected account yet", async () => {
    process.env.APPSELL_FINANCIAL_CONNECT_SELLER_IDS = SELLER_ID;

    const response = await POST(request(validBody));

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: "seller_not_ready_to_charge" });
    expect(createFinancialExternalCheckoutIntent).not.toHaveBeenCalled();
  });

  it("refuses to charge (409) when the store's account cannot charge yet", async () => {
    process.env.APPSELL_FINANCIAL_CONNECT_SELLER_IDS = SELLER_ID;
    resolveCheckoutPayoutDestination.mockResolvedValue({
      payout_destination: { provider_account_id: "acct_store_a", charges_enabled: false }
    });

    const response = await POST(request(validBody));

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: "seller_not_ready_to_charge" });
    expect(createFinancialExternalCheckoutIntent).not.toHaveBeenCalled();
  });

  it("fails closed (503) when the payout destination cannot be read", async () => {
    process.env.APPSELL_FINANCIAL_CONNECT_SELLER_IDS = SELLER_ID;
    resolveCheckoutPayoutDestination.mockRejectedValue(new Error("domain down"));

    const response = await POST(request(validBody));

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: "payout_destination_unavailable" });
    expect(createFinancialExternalCheckoutIntent).not.toHaveBeenCalled();
  });

  it("refuses (409) a price the commission would swallow, instead of sending a fee the Financial rejects", async () => {
    // launch: floor(50 * 5.5%) + 49 = 51, capped at 50 = the whole price.
    process.env.APPSELL_FINANCIAL_CONNECT_SELLER_IDS = SELLER_ID;
    resolveCheckoutPayoutDestination.mockResolvedValue(readyDestination());
    readPublicStorefront.mockResolvedValue(storefrontWithPrice(50));

    const response = await POST(request(validBody));

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: "product_price_below_platform_fee" });
    expect(createFinancialExternalCheckoutIntent).not.toHaveBeenCalled();
  });

  it("on the list, the same click repeated gets the same idempotency key, and another buyer a different one", async () => {
    vi.useFakeTimers({ now: new Date("2026-10-25T12:05:00Z"), toFake: ["Date"] });
    process.env.APPSELL_FINANCIAL_CONNECT_SELLER_IDS = SELLER_ID;
    resolveCheckoutPayoutDestination.mockResolvedValue(readyDestination());

    await POST(request(validBody));
    vi.setSystemTime(new Date("2026-10-25T12:20:00Z"));
    await POST(request(validBody));
    resolveBuyerIdentityMock.mockResolvedValue({ status: "authenticated", subject: "cognito-sub-other" });
    await POST(request({ ...validBody, buyer_email: "other@example.test" }));

    const [first, repeated, otherBuyer] = [0, 1, 2].map((call) => intentInput(call).idempotencyKey as string);
    expect(first).toEqual(repeated);
    expect(first).not.toEqual(otherBuyer);
    expect(first.startsWith("sell-connect-v1:")).toBe(true);
    expect(first.length).toBeLessThanOrEqual(160);
    // Nothing that identifies the buyer travels in clear in the key.
    expect(first).not.toContain("cognito-sub-default");
    expect(first).not.toContain("buyer@example.test");
  });

  it("on the list, a new 30-minute window or a new price gets a new key", async () => {
    vi.useFakeTimers({ now: new Date("2026-10-25T12:05:00Z"), toFake: ["Date"] });
    process.env.APPSELL_FINANCIAL_CONNECT_SELLER_IDS = SELLER_ID;
    resolveCheckoutPayoutDestination.mockResolvedValue(readyDestination());

    await POST(request(validBody));
    vi.setSystemTime(new Date("2026-10-25T12:31:00Z"));
    await POST(request(validBody));
    readPublicStorefront.mockResolvedValue(storefrontWithPrice(5990));
    await POST(request(validBody));

    const keys = [0, 1, 2].map((call) => intentInput(call).idempotencyKey);
    expect(new Set(keys).size).toBe(3);
  });
});
