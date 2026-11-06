import { NextResponse } from "next/server";

import { checkSellerNet } from "@/lib/minimum-price";

import { isRecord } from "./_route-shared";

// Trava do preço mínimo nas rotas do produtor (#546). Só vale para preço
// positivo em BRL: preço zero continua permitido no rascunho (a ativação e a
// publicação paga já exigem preço), e outra moeda não é vendida pelo checkout.
// Quem cobra é o create-session, que repete a conta como última trava.

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
  const offer = isRecord(draft) && isRecord(draft.product) ? draft.product.offer : undefined;
  if (!isRecord(offer) || offer.id !== offerId) return { kind: "invalid_draft" };
  const amount = offer.unit_amount_minor;
  const currency = offer.currency;
  if (amount === null || amount === undefined) return { kind: "ok" };
  if (typeof amount !== "number" || typeof currency !== "string") return { kind: "invalid_draft" };
  const minimumMinor = minimumForPrice(amount, currency);
  return minimumMinor === null ? { kind: "ok" } : { kind: "below_minimum", minimumMinor };
}

export function priceBelowMinimum(minimumMinor: number) {
  return NextResponse.json(
    {
      error: "invalid_product_input",
      field: "offer_unit_amount_minor",
      reason: "below_minimum",
      minimum_minor: minimumMinor
    },
    { status: 422, headers: { "cache-control": "no-store" } }
  );
}
