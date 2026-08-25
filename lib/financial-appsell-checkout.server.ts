import "server-only";

import { financialInternalConfig, trimTrailingSlash } from "./financial-internal-config.server";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const ENDPOINT_KEY = /^[A-Za-z0-9_-]{32,64}$/;

export class FinancialAppSellCheckoutConfigError extends Error {}
export class FinancialAppSellCheckoutError extends Error {
  constructor(
    message: string,
    readonly status: number
  ) {
    super(message);
  }
}

export function appSellCheckoutProvider(
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  return environment.APPSELL_CHECKOUT_PROVIDER === "financial_checkout"
    ? "financial_checkout"
    : "local_stripe";
}

export function appsellFinancialOperatorId(
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  const value = environment.APPSELL_FINANCIAL_OPERATOR_ID ?? "";
  if (!UUID.test(value)) throw new FinancialAppSellCheckoutConfigError("invalid_financial_operator_id");
  return value;
}

export function appsellCheckoutWebhookEndpointKey(
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  const value = environment.APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY ?? "";
  if (!ENDPOINT_KEY.test(value)) throw new FinancialAppSellCheckoutConfigError("invalid_checkout_webhook_endpoint_key");
  return value;
}

export interface CreateFinancialAppSellCheckoutIntentInput {
  operatorId: string;
  seller: { id: string; slug: string; name: string };
  buyer: { userId: string; email: string; identitySub: string };
  product: {
    externalProductId: string;
    externalOfferId: string | null;
    fulfillmentExternalProductId: string;
    title: string;
    description: string | null;
    imageUrl: string | null;
  };
  pricing: { amountMinor: number; currency: "BRL"; billingPeriod: "one_time" };
  connect: { accountId: string | null; applicationFeeAmountMinor: number | null };
  successUrl: string;
  cancelUrl: string;
  fulfillmentWebhookUrl: string;
  fulfillmentEndpointKey: string;
  idempotencyKey: string;
}

export interface FinancialAppSellCheckoutIntent {
  checkoutIntentId: string;
  checkoutUrl: string;
}

export async function createFinancialAppSellCheckoutIntent(
  input: CreateFinancialAppSellCheckoutIntentInput
): Promise<FinancialAppSellCheckoutIntent> {
  const config = financialInternalConfig();
  const response = await fetch(new URL("/financial/internal/appsell/checkout-intents", trimTrailingSlash(config.apiUrl)), {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": config.apiKey
    },
    body: JSON.stringify({
      source_system: "appsell",
      operator_id: input.operatorId,
      seller: input.seller,
      buyer: {
        user_id: input.buyer.userId,
        email: input.buyer.email,
        identity_sub: input.buyer.identitySub
      },
      product: {
        external_product_id: input.product.externalProductId,
        external_offer_id: input.product.externalOfferId,
        fulfillment_external_product_id: input.product.fulfillmentExternalProductId,
        title: input.product.title,
        description: input.product.description,
        image_url: input.product.imageUrl
      },
      pricing: {
        amount_minor: input.pricing.amountMinor,
        currency: input.pricing.currency,
        billing_period: input.pricing.billingPeriod
      },
      connect: {
        account_id: input.connect.accountId,
        application_fee_amount_minor: input.connect.applicationFeeAmountMinor
      },
      success_url: input.successUrl,
      cancel_url: input.cancelUrl,
      fulfillment_webhook_url: input.fulfillmentWebhookUrl,
      fulfillment_endpoint_key: input.fulfillmentEndpointKey,
      idempotency_key: input.idempotencyKey
    }),
    cache: "no-store"
  });
  const payload = await response.json().catch(() => null) as unknown;
  if (!response.ok) {
    throw new FinancialAppSellCheckoutError("financial_checkout_rejected", response.status);
  }
  if (!isRecord(payload) || typeof payload.checkout_intent_id !== "string" || typeof payload.checkout_url !== "string") {
    throw new FinancialAppSellCheckoutError("financial_checkout_invalid_response", 502);
  }
  return {
    checkoutIntentId: payload.checkout_intent_id,
    checkoutUrl: payload.checkout_url
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
