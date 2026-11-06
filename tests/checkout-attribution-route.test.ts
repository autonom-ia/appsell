import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

// #563: o checkout chama a atribuição da campanha depois de criar o intent. O
// que este arquivo prova é a regra da Dinheiro: se a atribuição lançar erro,
// síncrono ou assíncrono, o checkout responde exatamente o mesmo.
const {
  readPublicStorefront,
  resolveCheckoutPayoutDestination,
  createFinancialExternalCheckoutIntent,
  createFinancialExternalCheckoutSession,
  registrarAtribuicaoDoCheckout,
  atribuicaoFalhou,
  sessionsCreate,
  FakeFinancialConfigError,
  FakeFinancialCheckoutError,
  DomainApiError
} = vi.hoisted(() => {
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
    readPublicStorefront: vi.fn(),
    resolveCheckoutPayoutDestination: vi.fn(async () => ({ payout_destination: null })),
    createFinancialExternalCheckoutIntent: vi.fn(async () => ({
      checkoutIntentId: "0c0c0c0c-0c0c-4c0c-8c0c-0c0c0c0c0c0c",
      checkoutUrl: "https://financial.test/financial/public/external-checkout/0c0c0c0c-0c0c-4c0c-8c0c-0c0c0c0c0c0c"
    })),
    createFinancialExternalCheckoutSession: vi.fn(async () => "https://checkout.stripe.com/c/pay/cs_test_123"),
    registrarAtribuicaoDoCheckout: vi.fn(async () => undefined),
    atribuicaoFalhou: vi.fn(),
    sessionsCreate: vi.fn(async () => ({ url: "https://checkout.stripe.test/session/cs_test_123" })),
    FakeFinancialConfigError,
    FakeFinancialCheckoutError,
    DomainApiError
  };
});

vi.mock("@/lib/stripe-config.server", () => ({
  StripeConfigError: class extends Error {},
  stripeClient: () => ({ checkout: { sessions: { create: sessionsCreate } } }),
  stripeWebhookSecret: () => "whsec_test"
}));
vi.mock("@/lib/internal-api-client.server", () => ({
  readPublicStorefront,
  resolveCheckoutPayoutDestination,
  DomainApiError
}));
vi.mock("@/lib/buyer-identity.server", () => ({
  resolveBuyerIdentity: vi.fn(async () => ({ status: "authenticated", subject: "cognito-sub-default" }))
}));
vi.mock("@/lib/financial-external-checkout.server", () => ({
  checkoutProvider: () => {
    const value = process.env.APPSELL_CHECKOUT_PROVIDER;
    if (value === "financial_checkout" || value === "local_stripe") return value;
    throw new FakeFinancialConfigError("invalid_config: APPSELL_CHECKOUT_PROVIDER");
  },
  checkoutWebhookEndpointKey: () => process.env.APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY ?? "",
  createFinancialExternalCheckoutIntent,
  createFinancialExternalCheckoutSession,
  FinancialExternalCheckoutConfigError: FakeFinancialConfigError,
  FinancialExternalCheckoutError: FakeFinancialCheckoutError
}));
vi.mock("@/lib/tracking/checkout-attribution.server", () => ({
  registrarAtribuicaoDoCheckout,
  atribuicaoFalhou
}));

import { POST } from "../app/api/checkout/stripe/create-session/route";

const ORIGIN = "http://localhost:3000";
const SELLER = "autonomia-demo";
const PRODUCT = "codex-na-pratica";
const SELLER_ID = "11111111-1111-4111-8111-111111111111";
const INTENT_ID = "0c0c0c0c-0c0c-4c0c-8c0c-0c0c0c0c0c0c";
const URL_DO_STRIPE = "https://checkout.stripe.com/c/pay/cs_test_123";
const originalEnvironment = { ...process.env };

function vitrine() {
  return {
    schema_version: 1,
    seller: {
      id: SELLER_ID,
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
        price: { unit_amount_minor: 4990, currency: "BRL", billing_period: "one_time" }
      }
    ]
  };
}

function pedido(): NextRequest {
  return new Request(`${ORIGIN}/api/checkout/stripe/create-session`, {
    method: "POST",
    headers: { "content-type": "application/json", origin: ORIGIN },
    body: JSON.stringify({ seller_slug: SELLER, product_slug: PRODUCT, buyer_email: "buyer@example.test" })
  }) as unknown as NextRequest;
}

async function checkout() {
  const response = await POST(pedido());
  return { status: response.status, body: await response.json(), cache: response.headers.get("cache-control") };
}

describe("checkout do Financial com a atribuição da campanha (#563)", () => {
  beforeEach(() => {
    process.env.APPSELL_PUBLIC_ORIGIN = ORIGIN;
    process.env.APPSELL_CHECKOUT_PROVIDER = "financial_checkout";
    process.env.APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY = "endpointkeyendpointkeyendpointkey123456";
    process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = "{}";
    delete process.env.APPSELL_FINANCIAL_CONNECT_SELLER_IDS;
    readPublicStorefront.mockReset();
    readPublicStorefront.mockResolvedValue(vitrine());
    vi.clearAllMocks();
    registrarAtribuicaoDoCheckout.mockImplementation(async () => undefined);
  });

  afterEach(() => {
    process.env = { ...originalEnvironment };
  });

  it("registra a atribuição com a loja da vitrine e o id do intent, depois de criar a sessão", async () => {
    const resultado = await checkout();

    expect(resultado).toEqual({ status: 200, body: { url: URL_DO_STRIPE }, cache: "private, no-store" });
    expect(registrarAtribuicaoDoCheckout).toHaveBeenCalledTimes(1);
    const [requisicao, loja, intent] = registrarAtribuicaoDoCheckout.mock.calls[0] as unknown[];
    expect(requisicao).toBeDefined();
    expect(loja).toBe(SELLER_ID);
    expect(intent).toBe(INTENT_ID);
    const ordemDaSessao = createFinancialExternalCheckoutSession.mock.invocationCallOrder[0];
    const ordemDaAtribuicao = registrarAtribuicaoDoCheckout.mock.invocationCallOrder[0];
    expect(ordemDaAtribuicao).toBeGreaterThan(ordemDaSessao);
    expect(atribuicaoFalhou).not.toHaveBeenCalled();
  });

  it("se a atribuição rejeitar, o checkout responde igual e o erro só vai ao log", async () => {
    const semFalha = await checkout();
    const erro = new Error("boom");
    registrarAtribuicaoDoCheckout.mockImplementation(async () => {
      throw erro;
    });

    const comFalha = await checkout();

    expect(comFalha).toEqual(semFalha);
    expect(atribuicaoFalhou).toHaveBeenCalledTimes(1);
    expect(atribuicaoFalhou).toHaveBeenCalledWith(erro);
    expect(createFinancialExternalCheckoutIntent).toHaveBeenCalledTimes(2);
  });

  it("se a atribuição lançar de forma síncrona, o checkout responde igual", async () => {
    const semFalha = await checkout();
    registrarAtribuicaoDoCheckout.mockImplementation(() => {
      throw new TypeError("sync");
    });

    const comFalha = await checkout();

    expect(comFalha).toEqual(semFalha);
    expect(atribuicaoFalhou).toHaveBeenCalledTimes(1);
  });

  it("se o intent falhar, a atribuição nem é chamada e o erro do checkout é o de hoje", async () => {
    createFinancialExternalCheckoutIntent.mockRejectedValueOnce(new FakeFinancialCheckoutError("down", 502));

    const resultado = await checkout();

    expect(resultado.status).toBe(503);
    expect(resultado.body).toEqual({ error: "financial_checkout_unavailable" });
    expect(registrarAtribuicaoDoCheckout).not.toHaveBeenCalled();
  });

  it("se a sessão do Stripe falhar, a atribuição não é chamada", async () => {
    createFinancialExternalCheckoutSession.mockRejectedValueOnce(new FakeFinancialCheckoutError("down", 502));

    const resultado = await checkout();

    expect(resultado.status).toBe(503);
    expect(registrarAtribuicaoDoCheckout).not.toHaveBeenCalled();
  });

  it("no Stripe direto (local_stripe) a atribuição não é chamada", async () => {
    process.env.APPSELL_CHECKOUT_PROVIDER = "local_stripe";
    process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = JSON.stringify({
      [`${SELLER}/${PRODUCT}`]: { financial_product_key: "prod_codex_v1" }
    });

    const resultado = await checkout();

    expect(resultado.status).toBe(200);
    expect(sessionsCreate).toHaveBeenCalledTimes(1);
    expect(registrarAtribuicaoDoCheckout).not.toHaveBeenCalled();
  });
});
