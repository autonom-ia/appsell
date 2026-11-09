import "server-only";

import type { AppSellPlanKey } from "./appsell-pricing";
import { DomainApiError, internalServiceConfig } from "./internal-api-client.server";
import { signInternalEnvelope } from "./internal-envelope";

// #154, fatia 1: grava o pedido do checkout na api antes do Financial e o liga
// ao intent depois. Só o create-session chama, e só para as lojas do piloto
// (checkout-order-pilot.ts). Envelope do worker do checkout: capacidade e
// sujeito fixos, sem loja (a loja é a chave de busca vinda da vitrine).
const CAPACIDADE = "commerce.checkout.order";
const SUJEITO = "checkout-order-v1";
const TEMPO_MAXIMO_MS = 3_000;
const HEX = "0123456789abcdef";
const POSICOES_DO_HIFEN = [8, 13, 18, 23];

export interface PedidoParaGravar {
  sellerId: string;
  productId: string;
  publicationId: string;
  unitAmountMinor: number;
  currency: "BRL";
  billingPeriod: "one_time";
  platformFeeMinor: number | null;
  sellerNetMinor: number | null;
  planKey: AppSellPlanKey;
  buyerIdentitySub: string;
}

export interface PedidoGravado {
  orderId: string;
  offerId: string;
  offerVersion: number;
  chargedMinor: number;
  expiresAt: string;
}

export async function gravarPedidoDoCheckout(pedido: PedidoParaGravar): Promise<PedidoGravado> {
  const resposta = await chamar(`/v1/internal/sellers/${encodeURIComponent(pedido.sellerId)}/checkout-orders`, {
    product_id: pedido.productId,
    publication_id: pedido.publicationId,
    unit_amount_minor: pedido.unitAmountMinor,
    currency: pedido.currency,
    billing_period: pedido.billingPeriod,
    platform_fee_minor: pedido.platformFeeMinor,
    seller_net_minor: pedido.sellerNetMinor,
    plan_key: pedido.planKey,
    buyer_identity_sub: pedido.buyerIdentitySub
  });
  const gravado = lerPedidoGravado(resposta);
  if (!gravado) throw new DomainApiError(503, "invalid_checkout_order_response");
  return gravado;
}

export async function ligarPedidoAoIntent(sellerId: string, orderId: string, checkoutIntentId: string): Promise<void> {
  const caminho =
    `/v1/internal/sellers/${encodeURIComponent(sellerId)}/checkout-orders/${encodeURIComponent(orderId)}/intent`;
  await chamar(caminho, { checkout_intent_id: checkoutIntentId });
}

async function chamar(caminho: string, corpo: Record<string, unknown>): Promise<unknown> {
  const config = internalServiceConfig();
  const issuer = process.env.APPSELL_IDENTITY_ISSUER;
  if (!issuer) throw new DomainApiError(503, "missing_appsell_identity_issuer");
  const body = Buffer.from(JSON.stringify(corpo), "utf8");
  const { token } = signInternalEnvelope(
    {
      sessionId: "checkout-order",
      sessionEpoch: 1,
      identity: { issuer, sub: SUJEITO },
      capability: CAPACIDADE,
      sellerOrganizationId: null,
      method: "POST",
      path: caminho,
      query: Buffer.alloc(0),
      body
    },
    config.signing
  );
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TEMPO_MAXIMO_MS);
  try {
    const response = await fetch(new URL(caminho, config.apiOrigin), {
      method: "POST",
      body,
      headers: {
        "content-type": "application/json",
        "x-appsell-internal-envelope": token,
        "x-appsell-service-credential": config.serviceCredentialEncoded
      },
      cache: "no-store",
      signal: controller.signal
    });
    const payload = response.status === 204 ? null : await response.json().catch(() => null) as unknown;
    if (!response.ok) {
      const code = isRecord(payload) && typeof payload.detail === "string" ? payload.detail : "domain_api_error";
      throw new DomainApiError(response.status, code);
    }
    return payload;
  } catch (error) {
    if (error instanceof DomainApiError) throw error;
    throw new DomainApiError(503, "domain_api_unavailable");
  } finally {
    clearTimeout(timeout);
  }
}

function lerPedidoGravado(valor: unknown): PedidoGravado | null {
  if (!isRecord(valor)) return null;
  const { order_id, offer_id, offer_version, charged_minor, expires_at } = valor;
  if (!isUuid(order_id) || !isUuid(offer_id)) return null;
  if (!isPositiveInteger(offer_version) || !isPositiveInteger(charged_minor)) return null;
  if (typeof expires_at !== "string" || Number.isNaN(Date.parse(expires_at))) return null;
  return {
    orderId: order_id,
    offerId: offer_id,
    offerVersion: offer_version,
    chargedMinor: charged_minor,
    expiresAt: expires_at
  };
}

function isPositiveInteger(valor: unknown): valor is number {
  return typeof valor === "number" && Number.isSafeInteger(valor) && valor > 0;
}

// A forma canônica que a api devolve (`str(UUID)`): 36 caracteres minúsculos,
// hífens nas posições fixas.
function isUuid(valor: unknown): valor is string {
  if (typeof valor !== "string" || valor.length !== 36) return false;
  return [...valor].every((caractere, posicao) =>
    POSICOES_DO_HIFEN.includes(posicao) ? caractere === "-" : HEX.includes(caractere)
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
