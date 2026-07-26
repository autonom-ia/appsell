import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

const {
  sessionsCreate,
  readPublicStorefront,
  resolveCheckoutPayoutDestination,
  FakeStripeConfigError,
  DomainApiError,
  resolveBuyerIdentityMock
} = vi.hoisted(() => {
  class FakeStripeConfigError extends Error {}
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return {
    sessionsCreate: vi.fn(async () => ({ url: "https://checkout.stripe.test/session/cs_direct_1" })),
    readPublicStorefront: vi.fn(),
    resolveCheckoutPayoutDestination: vi.fn(),
    FakeStripeConfigError,
    DomainApiError,
    resolveBuyerIdentityMock: vi.fn(async () => ({ status: "authenticated", subject: "sub-1" }))
  };
});

vi.mock("@/lib/stripe-config.server", () => ({
  StripeConfigError: FakeStripeConfigError,
  stripeClient: () => ({ checkout: { sessions: { create: sessionsCreate } } }),
  stripeWebhookSecret: () => "whsec_test"
}));

vi.mock("@/lib/internal-api-client.server", () => ({
  readPublicStorefront,
  resolveCheckoutPayoutDestination,
  DomainApiError
}));

vi.mock("@/lib/buyer-identity.server", () => ({
  resolveBuyerIdentity: resolveBuyerIdentityMock
}));

import { POST } from "../app/api/checkout/stripe/create-session/route";

const ORIGIN = "http://localhost:3000";
const SELLER = "produtor-real";
const PRODUCT = "curso-real";
const SELLER_ID = "11111111-1111-4111-8111-111111111111";
const CONNECTED_ACCOUNT = "acct_1ProducerTest";

function storefront(unitAmountMinor: number) {
  return {
    schema_version: 1,
    seller: { id: SELLER_ID, slug: SELLER, name: "Produtor Real" },
    products: [
      {
        id: "22222222-2222-4222-8222-222222222222",
        slug: PRODUCT,
        title: "Curso Real",
        description: "Curso",
        publication_id: "33333333-3333-4333-8333-333333333333",
        publication_version: 1,
        price: { unit_amount_minor: unitAmountMinor, currency: "BRL", billing_period: "one_time" }
      }
    ]
  };
}

function request(): NextRequest {
  return new Request(`${ORIGIN}/api/checkout/stripe/create-session`, {
    method: "POST",
    headers: { "content-type": "application/json", origin: ORIGIN },
    body: JSON.stringify({ seller_slug: SELLER, product_slug: PRODUCT, buyer_email: "buyer@example.test" })
  }) as unknown as NextRequest;
}

const originalEnvironment = { ...process.env };

type CreateCall = [
  {
    payment_intent_data?: { application_fee_amount: number };
    line_items: Array<{ price_data: { unit_amount: number } }>;
  },
  { stripeAccount: string } | undefined
];

function firstCreateCall(): CreateCall {
  expect(sessionsCreate).toHaveBeenCalledTimes(1);
  return sessionsCreate.mock.calls[0] as unknown as CreateCall;
}

describe("POST /api/checkout/stripe/create-session (direct charge)", () => {
  beforeEach(() => {
    process.env.APPSELL_PUBLIC_ORIGIN = ORIGIN;
    process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = JSON.stringify({
      [`${SELLER}/${PRODUCT}`]: { financial_product_key: "prod_curso_real_v1" }
    });
    readPublicStorefront.mockReset();
    readPublicStorefront.mockResolvedValue(storefront(19_900));
    resolveCheckoutPayoutDestination.mockReset();
    sessionsCreate.mockClear();
    resolveBuyerIdentityMock.mockResolvedValue({ status: "authenticated", subject: "sub-1" });
  });

  afterEach(() => {
    process.env = { ...originalEnvironment };
  });

  it("creates the session ON the connected account with the server-computed fee", async () => {
    resolveCheckoutPayoutDestination.mockResolvedValue({
      payout_destination: {
        provider: "stripe",
        provider_account_id: CONNECTED_ACCOUNT,
        charges_enabled: true
      }
    });
    const response = await POST(request());
    expect(response.status).toBe(200);
    expect(resolveCheckoutPayoutDestination).toHaveBeenCalledWith(SELLER_ID);
    const [params, options] = firstCreateCall();
    // launch: floor(19900*550/10000)=1094 + 49 = 1143 — from the pricing
    // contract, never from the request.
    expect(params.payment_intent_data).toEqual({ application_fee_amount: 1_143 });
    expect(params.line_items[0].price_data.unit_amount).toBe(19_900);
    expect(options).toEqual({ stripeAccount: CONNECTED_ACCOUNT });
  });

  it("refuses to charge a seller whose account cannot charge yet", async () => {
    resolveCheckoutPayoutDestination.mockResolvedValue({
      payout_destination: {
        provider: "stripe",
        provider_account_id: CONNECTED_ACCOUNT,
        charges_enabled: false
      }
    });
    const response = await POST(request());
    expect(response.status).toBe(409);
    expect((await response.json()).error).toBe("seller_not_ready_to_charge");
    expect(sessionsCreate).not.toHaveBeenCalled();
  });

  it("fails CLOSED when the destination lookup is unavailable", async () => {
    // Charging without knowing the destination would settle a producer's sale
    // into the platform's own account — the custody #143 forbids.
    resolveCheckoutPayoutDestination.mockRejectedValue(
      new DomainApiError(503, "domain_api_unavailable")
    );
    const response = await POST(request());
    expect(response.status).toBe(503);
    expect((await response.json()).error).toBe("payout_destination_unavailable");
    expect(sessionsCreate).not.toHaveBeenCalled();
  });

  it("keeps the legacy platform charge for a seller with no destination", async () => {
    resolveCheckoutPayoutDestination.mockResolvedValue({ payout_destination: null });
    const response = await POST(request());
    expect(response.status).toBe(200);
    const [params, options] = firstCreateCall();
    expect(params.payment_intent_data).toBeUndefined();
    expect(options).toBeUndefined();
  });
});
