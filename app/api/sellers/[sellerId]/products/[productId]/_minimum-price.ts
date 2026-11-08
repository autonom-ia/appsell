import { NextResponse } from "next/server";

import { isFinancialConnectSeller } from "@/lib/financial-connect-checkout";
import { configuredCheckoutProvider } from "@/lib/financial-external-checkout.server";
import { resolveCheckoutPayoutDestination } from "@/lib/internal-api-client.server";
import { checkSellerNet } from "@/lib/minimum-price";

import { isRecord } from "./_route-shared";

// Trava do preço mínimo nas rotas do produtor (#546). Só vale para preço
// positivo em BRL: preço zero continua permitido no rascunho (a ativação e a
// publicação paga já exigem preço), e outra moeda não é vendida pelo checkout.
// Quem cobra é o create-session, que repete a conta como última trava.
//
// Escopo: a trava vale só para a loja que paga comissão. Sem comissão, a loja
// recebe o valor inteiro e não há preço em que ela fique sem nada (é o caso da
// Hub2You, AGENTS.md §7). A regra de quem paga comissão é a do create-session.

export function minimumForPrice(unitAmountMinor: number, currency: string): number | null {
  if (currency !== "BRL" || !Number.isSafeInteger(unitAmountMinor) || unitAmountMinor <= 0) {
    return null;
  }
  const result = checkSellerNet({ amountMinor: unitAmountMinor, currency: "BRL" });
  return result.ok ? null : result.minimumMinor;
}

export type DraftOfferMinimum =
  | { kind: "ok" }
  | { kind: "below_minimum"; minimumMinor: number }
  | { kind: "invalid_draft" };

// Lê a oferta do rascunho devolvido pela api. Resposta fora do formato falha
// fechado: sem saber o preço, não ativa nem publica.
export function draftOfferMinimum(draft: unknown, offerId: string): DraftOfferMinimum {
  const offer = findDraftOffer(draft, offerId);
  if (offer === null) return { kind: "invalid_draft" };
  const amount = offer.unit_amount_minor;
  const currency = offer.currency;
  if (amount === null || amount === undefined) return { kind: "ok" };
  if (typeof amount !== "number" || typeof currency !== "string") return { kind: "invalid_draft" };
  const minimumMinor = minimumForPrice(amount, currency);
  return minimumMinor === null ? { kind: "ok" } : { kind: "below_minimum", minimumMinor };
}

// Desde o #553 a api devolve `offer` (a padrão) e `offers` (todas). A oferta
// pedida é procurada pelo id em `offers`; `offer` cobre a api de antes.
function findDraftOffer(draft: unknown, offerId: string): Record<string, unknown> | null {
  const product = isRecord(draft) && isRecord(draft.product) ? draft.product : null;
  if (product === null) return null;
  const offers = Array.isArray(product.offers) ? product.offers : [];
  const listed = offers.find((offer) => isRecord(offer) && offer.id === offerId);
  if (isRecord(listed)) return listed;
  return isRecord(product.offer) && product.offer.id === offerId ? product.offer : null;
}

// O campo segue o nome que a rota recebe: o PATCH do produto manda
// offer_unit_amount_minor; o POST de oferta nova, unit_amount_minor.
export function priceBelowMinimum(minimumMinor: number, field = "offer_unit_amount_minor") {
  return NextResponse.json(
    {
      error: "invalid_product_input",
      field,
      reason: "below_minimum",
      minimum_minor: minimumMinor
    },
    { status: 422, headers: { "cache-control": "no-store" } }
  );
}

// A loja paga comissão? Mesma regra do create-session:
// - financial_checkout: só a loja da lista Connect manda conta e comissão;
// - local_stripe: a comissão existe quando a loja tem conta de repasse.
// Provedor ausente ou inválido: a trava vale (o checkout recusa a venda de
// qualquer jeito). Erro ao ler a conta de repasse sobe para a rota, que falha
// fechado. Chame só depois de a rota confirmar que a loja é de quem pede.
export async function sellerChargesCommission(sellerId: string): Promise<boolean> {
  const provider = configuredCheckoutProvider();
  if (provider === "financial_checkout") return isFinancialConnectSeller(sellerId);
  if (provider === "local_stripe") {
    const { payout_destination: destination } = await resolveCheckoutPayoutDestination(sellerId);
    return destination !== null && destination !== undefined;
  }
  return true;
}
