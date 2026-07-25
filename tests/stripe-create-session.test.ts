import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

// Hoisted so the (hoisted) vi.mock factories below can reference them.
const {
  sessionsCreate,
  readPublicStorefront,
  FakeStripeConfigError,
  DomainApiError,
  resolveBuyerIdentitySubMock
} = vi.hoisted(() => {
  class FakeStripeConfigError extends Error {}
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return {
    sessionsCreate: vi.fn(async () => ({ url: "https://checkout.stripe.test/session/cs_test_123" })),
    readPublicStorefront: vi.fn(),
    FakeStripeConfigError,
    DomainApiError,
    resolveBuyerIdentitySubMock: vi.fn(async () => null as string | null)
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
  DomainApiError
}));

// Identity handoff: the resolved subject is what puts the entitlement on the
// buyer's real login instead of the synthetic registration:<e-mail> one.
vi.mock("@/lib/buyer-identity.server", () => ({
  resolveBuyerIdentitySub: resolveBuyerIdentitySubMock
}));

import { POST } from "../app/api/checkout/stripe/create-session/route";

const ORIGIN = "http://localhost:3000";
const SELLER = "autonomia-demo";
const PRODUCT = "codex-na-pratica";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function storefrontWithPrice(unitAmountMinor: number | null, billingPeriod = "one_time", currency = "BRL") {
  return {
    schema_version: 1,
    seller: { id: "11111111-1111-4111-8111-111111111111", slug: SELLER, name: "Autonomia Demo" },
    products: [
      {
        id: "22222222-2222-4222-8222-222222222222",
        slug: PRODUCT,
        title: "Codex na Prática",
        description: "Curso",
        publication_id: "33333333-3333-4333-8333-333333333333",
        publication_version: 1,
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
    resolveBuyerIdentitySubMock.mockReset();
    resolveBuyerIdentitySubMock.mockResolvedValue(null);
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
    resolveBuyerIdentitySubMock.mockResolvedValue("cognito-sub-abc123");
    readPublicStorefront.mockResolvedValue(storefrontWithPrice(100));
    const response = await POST(request(validBody));
    expect(response.status).toBe(200);
    const params = (sessionsCreate.mock.calls[0] as unknown[])[0] as { metadata: Record<string, string> };
    expect(params.metadata.buyer_identity_sub).toBe("cognito-sub-abc123");
    // Resolved against the e-mail actually being charged, normalized.
    expect(resolveBuyerIdentitySubMock).toHaveBeenCalledWith(expect.anything(), "buyer@example.test");
  });

  it("marks an anonymous checkout with an empty subject", async () => {
    // Stripe metadata cannot hold null; "" is what the webhook maps back to
    // null so the worker's registration:<e-mail> fallback applies.
    resolveBuyerIdentitySubMock.mockResolvedValue(null);
    readPublicStorefront.mockResolvedValue(storefrontWithPrice(100));
    await POST(request(validBody));
    const params = (sessionsCreate.mock.calls[0] as unknown[])[0] as { metadata: Record<string, string> };
    expect(params.metadata.buyer_identity_sub).toBe("");
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
