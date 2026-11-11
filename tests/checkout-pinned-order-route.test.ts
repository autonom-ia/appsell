import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

// #154, fatia 1: o create-session grava o pedido antes do Financial, só para
// as lojas do piloto APPSELL_PINNED_ORDER_SELLER_IDS. O que este arquivo prova:
// - fora do piloto (lista vazia, ou a loja não listada) nada muda: o cliente
//   do pedido nem é chamado e o payload ao Financial é o mesmo;
// - no piloto, o pedido é gravado com os valores calculados no servidor, antes
//   do intent, e ligado ao intent depois;
// - falha ao gravar responde antes de qualquer cobrança; preço mudado é 409;
// - falha ao ligar só vai ao log: o comprador segue para o pagamento.
const {
  readPublicStorefront,
  resolveCheckoutPayoutDestination,
  createFinancialExternalCheckoutIntent,
  createFinancialExternalCheckoutSession,
  registrarAtribuicaoDoCheckout,
  atribuicaoFalhou,
  afterMock,
  sessionsCreate,
  FakeFinancialConfigError,
  FakeFinancialCheckoutError,
  DomainApiError,
  gravarPedidoDoCheckout,
  ligarPedidoAoIntent
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
    afterMock: vi.fn(),
    sessionsCreate: vi.fn(async () => ({ url: "https://checkout.stripe.test/session/cs_test_123" })),
    FakeFinancialConfigError,
    FakeFinancialCheckoutError,
    DomainApiError,
    gravarPedidoDoCheckout: vi.fn(),
    ligarPedidoAoIntent: vi.fn()
  };
});

// O `after` real só existe dentro de uma requisição do Next. Aqui ele guarda o
// callback, e o teste decide quando rodar: assim dá para ver a resposta antes.
vi.mock("next/server", async (original) => ({
  ...(await original<typeof import("next/server")>()),
  after: afterMock
}));
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

vi.mock("@/lib/checkout-order.server", () => ({ gravarPedidoDoCheckout, ligarPedidoAoIntent }));

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

const ORDER_ID = "66666666-6666-4666-8666-666666666666";
const ACCOUNT = "acct_pinnedOrderTest";

function pedidoGravado() {
  return {
    orderId: ORDER_ID,
    offerId: "77777777-7777-4777-8777-777777777777",
    offerVersion: 2,
    chargedMinor: 4990,
    expiresAt: "2026-11-09T12:31:00+00:00"
  };
}

describe("create-session com o pedido pinado (#154, fatia 1)", () => {
  beforeEach(() => {
    process.env.APPSELL_PUBLIC_ORIGIN = ORIGIN;
    process.env.APPSELL_CHECKOUT_PROVIDER = "financial_checkout";
    process.env.APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY = "endpointkeyendpointkeyendpointkey123456";
    process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = "{}";
    delete process.env.APPSELL_FINANCIAL_CONNECT_SELLER_IDS;
    delete process.env.APPSELL_PINNED_ORDER_SELLER_IDS;
    vi.clearAllMocks();
    readPublicStorefront.mockReset();
    readPublicStorefront.mockResolvedValue(vitrine());
    resolveCheckoutPayoutDestination.mockResolvedValue({ payout_destination: null });
    afterMock.mockImplementation(() => undefined);
    gravarPedidoDoCheckout.mockReset();
    gravarPedidoDoCheckout.mockResolvedValue(pedidoGravado());
    ligarPedidoAoIntent.mockReset();
    ligarPedidoAoIntent.mockResolvedValue(undefined);
  });

  afterEach(() => {
    process.env = { ...originalEnvironment };
    vi.restoreAllMocks();
  });

  function payloadDoFinancial() {
    const chamada = createFinancialExternalCheckoutIntent.mock.calls.at(-1) as unknown[] | undefined;
    const payload = { ...(chamada?.[0] as Record<string, unknown>) };
    // A chave fora do Connect é um UUID novo por POST, hoje e depois.
    delete payload.idempotencyKey;
    return payload;
  }

  it.each([undefined, "", "99999999-9999-4999-8999-999999999999"])(
    "fora do piloto (%j) nada muda: sem pedido, mesmo payload",
    async (lista) => {
      const linhaDeBase = await checkout();
      const payloadDeBase = payloadDoFinancial();
      if (lista !== undefined) process.env.APPSELL_PINNED_ORDER_SELLER_IDS = lista;

      const resultado = await checkout();

      expect(resultado).toEqual(linhaDeBase);
      expect(resultado).toEqual({ status: 200, body: { url: URL_DO_STRIPE }, cache: "private, no-store" });
      expect(payloadDoFinancial()).toEqual(payloadDeBase);
      expect(gravarPedidoDoCheckout).not.toHaveBeenCalled();
      expect(ligarPedidoAoIntent).not.toHaveBeenCalled();
    }
  );

  it("no piloto grava o pedido antes do intent e o liga ao intent depois, sem mudar o payload", async () => {
    await checkout();
    const payloadDeBase = payloadDoFinancial();
    vi.clearAllMocks();
    process.env.APPSELL_PINNED_ORDER_SELLER_IDS = SELLER_ID;

    const resultado = await checkout();

    expect(resultado).toEqual({ status: 200, body: { url: URL_DO_STRIPE }, cache: "private, no-store" });
    expect(gravarPedidoDoCheckout).toHaveBeenCalledTimes(1);
    expect(gravarPedidoDoCheckout).toHaveBeenCalledWith({
      sellerId: SELLER_ID,
      productId: "22222222-2222-4222-8222-222222222222",
      publicationId: "33333333-3333-4333-8333-333333333333",
      unitAmountMinor: 4990,
      currency: "BRL",
      billingPeriod: "one_time",
      platformFeeMinor: null,
      sellerNetMinor: null,
      planKey: "launch",
      buyerIdentitySub: "cognito-sub-default"
    });
    expect(gravarPedidoDoCheckout.mock.invocationCallOrder[0]).toBeLessThan(
      createFinancialExternalCheckoutIntent.mock.invocationCallOrder[0]
    );
    expect(ligarPedidoAoIntent).toHaveBeenCalledWith(SELLER_ID, ORDER_ID, INTENT_ID);
    expect(payloadDoFinancial()).toEqual(payloadDeBase);
  });

  it("loja Connect no piloto grava a comissão e o líquido de checkSellerNet", async () => {
    process.env.APPSELL_FINANCIAL_CONNECT_SELLER_IDS = SELLER_ID;
    process.env.APPSELL_PINNED_ORDER_SELLER_IDS = SELLER_ID;
    resolveCheckoutPayoutDestination.mockResolvedValue({
      payout_destination: { provider: "stripe", provider_account_id: ACCOUNT, charges_enabled: true, payouts_enabled: true }
    } as never);

    const resultado = await checkout();

    expect(resultado.status).toBe(200);
    // 4990 no plano launch: 5,5% (274) + 49 = 323 de comissão, 4667 líquido.
    expect(gravarPedidoDoCheckout).toHaveBeenCalledWith(
      expect.objectContaining({ platformFeeMinor: 323, sellerNetMinor: 4667 })
    );
    const payload = payloadDoFinancial();
    expect(payload.connect).toEqual({ accountId: ACCOUNT, applicationFeeAmountMinor: 323 });
  });

  it("preço mudado responde 409 price_changed e não chama o Financial", async () => {
    process.env.APPSELL_PINNED_ORDER_SELLER_IDS = SELLER_ID;
    gravarPedidoDoCheckout.mockRejectedValueOnce(new DomainApiError(409, "price_changed"));

    const resultado = await checkout();

    expect(resultado).toEqual({ status: 409, body: { error: "price_changed" }, cache: "private, no-store" });
    expect(createFinancialExternalCheckoutIntent).not.toHaveBeenCalled();
  });

  it.each([
    new DomainApiError(503, "domain_api_unavailable"),
    new DomainApiError(403, "forbidden"),
    new DomainApiError(503, "invalid_checkout_order_response"),
    new TypeError("inesperado")
  ])("falha ao gravar (%s) responde 503 antes de qualquer cobrança", async (erro) => {
    process.env.APPSELL_PINNED_ORDER_SELLER_IDS = SELLER_ID;
    gravarPedidoDoCheckout.mockRejectedValueOnce(erro);

    const resultado = await checkout();

    expect(resultado).toEqual({ status: 503, body: { error: "checkout_order_unavailable" }, cache: "private, no-store" });
    expect(createFinancialExternalCheckoutIntent).not.toHaveBeenCalled();
  });

  it("falha ao ligar ao intent só vai ao log, sem o id do intent nem o comprador", async () => {
    process.env.APPSELL_PINNED_ORDER_SELLER_IDS = SELLER_ID;
    ligarPedidoAoIntent.mockRejectedValueOnce(new DomainApiError(409, "checkout_order_not_open"));
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);

    const resultado = await checkout();

    expect(resultado).toEqual({ status: 200, body: { url: URL_DO_STRIPE }, cache: "private, no-store" });
    expect(log).toHaveBeenCalledTimes(1);
    const linha = String(log.mock.calls[0][0]);
    expect(JSON.parse(linha)).toEqual({
      level: "error",
      marker: "appsell.checkout_order.attach_failed",
      seller_id: SELLER_ID,
      order_id: ORDER_ID,
      reason: "checkout_order_not_open"
    });
    expect(linha).not.toContain(INTENT_ID);
    expect(linha).not.toContain("cognito-sub-default");
  });

  it("se o intent falhar no piloto, o pedido fica aberto e não é ligado", async () => {
    process.env.APPSELL_PINNED_ORDER_SELLER_IDS = SELLER_ID;
    createFinancialExternalCheckoutIntent.mockRejectedValueOnce(new FakeFinancialCheckoutError("down", 502));

    const resultado = await checkout();

    expect(resultado.body).toEqual({ error: "financial_checkout_unavailable" });
    expect(gravarPedidoDoCheckout).toHaveBeenCalledTimes(1);
    expect(ligarPedidoAoIntent).not.toHaveBeenCalled();
  });

  it("no Stripe direto (local_stripe) o pedido não é gravado", async () => {
    process.env.APPSELL_PINNED_ORDER_SELLER_IDS = SELLER_ID;
    process.env.APPSELL_CHECKOUT_PROVIDER = "local_stripe";
    process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = JSON.stringify({
      [`${SELLER}/${PRODUCT}`]: { financial_product_key: "prod_codex_v1" }
    });

    const resultado = await checkout();

    expect(resultado.status).toBe(200);
    expect(gravarPedidoDoCheckout).not.toHaveBeenCalled();
  });
});
