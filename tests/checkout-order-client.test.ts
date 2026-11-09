import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

// #154, fatia 1: o cliente do pedido do checkout. O que este arquivo prova:
// - o piloto é uma lista de lojas, vazia por padrão;
// - o envelope é do worker do checkout: capacidade e sujeito fixos, sem loja;
// - a resposta da api é conferida campo a campo antes de alguém confiar nela;
// - recusa e queda viram DomainApiError com o código da api.
const { signInternalEnvelope, DomainApiError } = vi.hoisted(() => {
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return {
    signInternalEnvelope: vi.fn((...args: unknown[]) => ({ token: "envelope-token", args })),
    DomainApiError
  };
});

vi.mock("@/lib/internal-api-client.server", () => ({
  internalServiceConfig: () => ({
    apiOrigin: "http://api.test",
    signing: { issuer: "bff", audience: "api", signingKey: { id: "k", key: Buffer.alloc(32) } },
    serviceCredentialEncoded: "service-credential"
  }),
  DomainApiError
}));
vi.mock("@/lib/internal-envelope", () => ({ signInternalEnvelope }));

import { gravarPedidoDoCheckout, ligarPedidoAoIntent } from "../lib/checkout-order.server";
import { pedidoPinadoLiberado } from "../lib/checkout-order-pilot";

const SELLER_ID = "11111111-1111-4111-8111-111111111111";
const ORDER_ID = "66666666-6666-4666-8666-666666666666";
const OFFER_ID = "77777777-7777-4777-8777-777777777777";
const originalEnvironment = { ...process.env };

const pedido = {
  sellerId: SELLER_ID,
  productId: "22222222-2222-4222-8222-222222222222",
  publicationId: "33333333-3333-4333-8333-333333333333",
  unitAmountMinor: 4990,
  currency: "BRL" as const,
  billingPeriod: "one_time" as const,
  platformFeeMinor: 323,
  sellerNetMinor: 4667,
  planKey: "launch" as const,
  buyerIdentitySub: "cognito-sub-1"
};

function resposta(status: number, corpo: unknown) {
  return new Response(corpo === undefined ? null : JSON.stringify(corpo), {
    status,
    headers: { "content-type": "application/json" }
  });
}

const gravado = {
  order_id: ORDER_ID,
  offer_id: OFFER_ID,
  offer_version: 2,
  charged_minor: 4990,
  expires_at: "2026-11-09T12:31:00+00:00"
};

describe("piloto do pedido pinado", () => {
  it("vazio por padrão: nenhuma loja liga", () => {
    expect(pedidoPinadoLiberado(SELLER_ID, {})).toBe(false);
    expect(pedidoPinadoLiberado(SELLER_ID, { APPSELL_PINNED_ORDER_SELLER_IDS: "" })).toBe(false);
  });

  it("liga só a loja listada, com espaço e caixa diferentes", () => {
    const env = { APPSELL_PINNED_ORDER_SELLER_IDS: ` ${SELLER_ID.toUpperCase()} , outra` };
    expect(pedidoPinadoLiberado(SELLER_ID, env)).toBe(true);
    expect(pedidoPinadoLiberado("99999999-9999-4999-8999-999999999999", env)).toBe(false);
  });
});

describe("cliente do pedido do checkout", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    process.env.APPSELL_IDENTITY_ISSUER = "https://identity.test";
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    signInternalEnvelope.mockClear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    process.env = { ...originalEnvironment };
  });

  it("grava com o envelope do worker, sem loja, e devolve o pedido", async () => {
    fetchMock.mockResolvedValueOnce(resposta(201, gravado));

    const resultado = await gravarPedidoDoCheckout(pedido);

    expect(resultado).toEqual({
      orderId: ORDER_ID,
      offerId: OFFER_ID,
      offerVersion: 2,
      chargedMinor: 4990,
      expiresAt: "2026-11-09T12:31:00+00:00"
    });
    const [url, init] = fetchMock.mock.calls[0] as [URL, RequestInit];
    expect(String(url)).toBe(`http://api.test/v1/internal/sellers/${SELLER_ID}/checkout-orders`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      product_id: pedido.productId,
      publication_id: pedido.publicationId,
      unit_amount_minor: 4990,
      currency: "BRL",
      billing_period: "one_time",
      platform_fee_minor: 323,
      seller_net_minor: 4667,
      plan_key: "launch",
      buyer_identity_sub: "cognito-sub-1"
    });
    const headers = init.headers as Record<string, string>;
    expect(headers["x-appsell-internal-envelope"]).toBe("envelope-token");
    expect(headers["x-appsell-service-credential"]).toBe("service-credential");
    const envelope = signInternalEnvelope.mock.calls[0][0] as Record<string, unknown>;
    expect(envelope).toMatchObject({
      capability: "commerce.checkout.order",
      sellerOrganizationId: null,
      identity: { issuer: "https://identity.test", sub: "checkout-order-v1" },
      method: "POST",
      path: `/v1/internal/sellers/${SELLER_ID}/checkout-orders`
    });
    // O corpo assinado é o mesmo enviado.
    expect(String(envelope.body)).toBe(String(init.body));
  });

  it("409 da api vira DomainApiError com o código price_changed", async () => {
    fetchMock.mockResolvedValueOnce(resposta(409, { detail: "price_changed" }));

    await expect(gravarPedidoDoCheckout(pedido)).rejects.toMatchObject({ status: 409, code: "price_changed" });
  });

  it("queda da api vira 503", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));

    await expect(gravarPedidoDoCheckout(pedido)).rejects.toMatchObject({ status: 503, code: "domain_api_unavailable" });
  });

  it.each([
    { ...gravado, charged_minor: "4990" },
    { ...gravado, order_id: "nao-e-uuid" },
    { ...gravado, offer_version: 0 },
    { ...gravado, expires_at: 1 },
    null
  ])("resposta fora do contrato vira 503: %j", async (corpo) => {
    fetchMock.mockResolvedValueOnce(resposta(201, corpo));

    await expect(gravarPedidoDoCheckout(pedido)).rejects.toMatchObject({ status: 503, code: "invalid_checkout_order_response" });
  });

  it("ligar ao intent manda o id do intent e aceita 204", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

    await expect(ligarPedidoAoIntent(SELLER_ID, ORDER_ID, "intent-1")).resolves.toBeUndefined();

    const [url, init] = fetchMock.mock.calls[0] as [URL, RequestInit];
    expect(String(url)).toBe(`http://api.test/v1/internal/sellers/${SELLER_ID}/checkout-orders/${ORDER_ID}/intent`);
    expect(JSON.parse(String(init.body))).toEqual({ checkout_intent_id: "intent-1" });
  });

  it("ligar recusado vira DomainApiError", async () => {
    fetchMock.mockResolvedValueOnce(resposta(409, { detail: "checkout_order_not_open" }));

    await expect(ligarPedidoAoIntent(SELLER_ID, ORDER_ID, "intent-1")).rejects.toMatchObject({
      status: 409,
      code: "checkout_order_not_open"
    });
  });
});
