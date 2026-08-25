import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  checkoutProvider,
  checkoutWebhookEndpointKey,
  createFinancialExternalCheckoutIntent,
  financialCheckoutOperatorId,
  FinancialExternalCheckoutConfigError,
  FinancialExternalCheckoutError
} from "../lib/financial-external-checkout.server";

const endpointKey = "endpointkeyendpointkeyendpointkey123456";

describe("Financial external checkout client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    delete process.env.APPSELL_CHECKOUT_PROVIDER;
    delete process.env.APPSELL_FINANCIAL_OPERATOR_ID;
    delete process.env.APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY;
    delete process.env.FINANCIAL_API_URL;
    delete process.env.FINANCIAL_INTERNAL_API_KEY;
  });

  it("defaults to the local Stripe checkout unless explicitly enabled", () => {
    expect(checkoutProvider({})).toBe("local_stripe");
    expect(checkoutProvider({ APPSELL_CHECKOUT_PROVIDER: "local_stripe" })).toBe("local_stripe");
    expect(checkoutProvider({ APPSELL_CHECKOUT_PROVIDER: "financial_checkout" })).toBe("financial_checkout");
    expect(checkoutProvider({ APPSELL_CHECKOUT_PROVIDER: "unexpected" })).toBe("local_stripe");
  });

  it("fails closed for missing production wiring", () => {
    expect(() => financialCheckoutOperatorId({ APPSELL_FINANCIAL_OPERATOR_ID: "not-a-uuid" }))
      .toThrow(FinancialExternalCheckoutConfigError);
    expect(() => checkoutWebhookEndpointKey({ APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY: "short" }))
      .toThrow(FinancialExternalCheckoutConfigError);
  });

  it("posts the product snapshot to Financial and returns its hosted checkout URL", async () => {
    process.env.FINANCIAL_API_URL = "https://financial.test/";
    process.env.FINANCIAL_INTERNAL_API_KEY = "internal-key";
    const fetch = vi.fn(async () => new Response(JSON.stringify({
      checkout_intent_id: "order-1",
      checkout_url: "https://financial.test/financial/public/external-checkout/order-1"
    }), { status: 201 }));
    vi.stubGlobal("fetch", fetch);

    await expect(createFinancialExternalCheckoutIntent({
      operatorId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      seller: { id: "seller-1", slug: "produtor", name: "Produtor" },
      buyer: { userId: "buyer-sub", email: "buyer@example.test", identitySub: "buyer-sub" },
      product: {
        externalProductId: "product-1",
        externalOfferId: "offer-1",
        fulfillmentExternalProductId: "product-1",
        title: "Curso de exemplo",
        description: "Descricao",
        imageUrl: "https://sell.test/image.png"
      },
      pricing: { amountMinor: 19900, currency: "BRL", billingPeriod: "one_time" },
      connect: { accountId: "acct_1Seller", applicationFeeAmountMinor: 1143 },
      successUrl: "https://sell.test/library?checkout=success",
      cancelUrl: "https://sell.test/s/produtor/p/curso?checkout=cancelled",
      fulfillmentWebhookUrl: "https://sell.test/api/webhooks/checkouts/endpoint",
      fulfillmentEndpointKey: endpointKey,
      idempotencyKey: "buyer-course-001"
    })).resolves.toEqual({
      checkoutIntentId: "order-1",
      checkoutUrl: "https://financial.test/financial/public/external-checkout/order-1"
    });

    expect(fetch).toHaveBeenCalledOnce();
    const [url, init] = fetch.mock.calls[0] as unknown as [URL, RequestInit];
    expect(url.toString()).toBe("https://financial.test/financial/internal/external-checkout/checkout-intents");
    expect(init.headers).toMatchObject({
      "content-type": "application/json",
      "x-api-key": "internal-key"
    });
    expect(JSON.parse(String(init.body))).toMatchObject({
      source_system: "external_checkout",
      operator_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      product: {
        external_product_id: "product-1",
        external_offer_id: "offer-1",
        fulfillment_external_product_id: "product-1"
      },
      pricing: {
        amount_minor: 19900,
        currency: "BRL",
        billing_period: "one_time"
      },
      fulfillment_endpoint_key: endpointKey
    });
  });

  it("rejects non-2xx or malformed Financial responses", async () => {
    process.env.FINANCIAL_INTERNAL_API_KEY = "internal-key";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: "nope" }), { status: 409 })));
    await expect(createFinancialExternalCheckoutIntent({
      operatorId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      seller: { id: "seller-1", slug: "produtor", name: "Produtor" },
      buyer: { userId: "buyer-sub", email: "buyer@example.test", identitySub: "buyer-sub" },
      product: {
        externalProductId: "product-1",
        externalOfferId: null,
        fulfillmentExternalProductId: "product-1",
        title: "Curso de exemplo",
        description: null,
        imageUrl: null
      },
      pricing: { amountMinor: 19900, currency: "BRL", billingPeriod: "one_time" },
      connect: { accountId: null, applicationFeeAmountMinor: null },
      successUrl: "https://sell.test/library?checkout=success",
      cancelUrl: "https://sell.test/s/produtor/p/curso?checkout=cancelled",
      fulfillmentWebhookUrl: "https://sell.test/api/webhooks/checkouts/endpoint",
      fulfillmentEndpointKey: endpointKey,
      idempotencyKey: "buyer-course-001"
    })).rejects.toMatchObject(new FinancialExternalCheckoutError("financial_checkout_rejected", 409));
  });
});
