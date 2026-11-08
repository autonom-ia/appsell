import type { AppSellPlanKey } from "./appsell-pricing";
import { DEFAULT_CHECKOUT_PLAN, directChargeApplicationFeeCents } from "./checkout-fee";
import { MAX_UNIT_AMOUNT_MINOR } from "./offer-price";

// Preço mínimo de uma venda (#546). Fonte única: o editor, as rotas de preço,
// de status e de publicação, e o create-session (a última trava) perguntam
// aqui. A regra tem duas partes:
//   1. o valor cobrado (preço menos desconto) passa do mínimo de cobrança da
//      Stripe em BRL;
//   2. o líquido da loja (cobrado menos comissão menos custo que a loja paga)
//      é positivo. A comissão sai de directChargeApplicationFeeCents, a mesma
//      conta do checkout.
// A api Python não valida isto: quem cria oferta pela chave interna e cobra
// sem passar pelo web só é barrado no create-session.

// Mínimo que a Stripe aceita cobrar em BRL (R$0,50).
export const STRIPE_MINIMUM_CHARGE_BRL_MINOR = 50;

export type MinimumPriceCurrency = "BRL";

export interface SellerNetInput {
  amountMinor: number;
  discountMinor?: number;
  sellerBorneCostMinor?: number;
  currency: MinimumPriceCurrency;
  planKey?: AppSellPlanKey;
}

export type SellerNetResult =
  | { ok: true; netMinor: number; feeMinor: number }
  | { ok: false; reason: "below_minimum"; minimumMinor: number; netMinor: number };

export function checkSellerNet(input: SellerNetInput): SellerNetResult {
  const {
    amountMinor,
    discountMinor = 0,
    sellerBorneCostMinor = 0,
    currency,
    planKey = DEFAULT_CHECKOUT_PLAN
  } = input;
  assertBRL(currency);
  assertMinor("amountMinor", amountMinor, 1);
  assertMinor("discountMinor", discountMinor, 0);
  assertMinor("sellerBorneCostMinor", sellerBorneCostMinor, 0);
  if (discountMinor > amountMinor) {
    throw new RangeError("discountMinor must not exceed amountMinor");
  }

  const charged = amountMinor - discountMinor;
  const feeMinor = feeOf(charged, planKey);
  const netMinor = charged - feeMinor - sellerBorneCostMinor;
  if (charged >= STRIPE_MINIMUM_CHARGE_BRL_MINOR && netMinor > 0) {
    return { ok: true, netMinor, feeMinor };
  }
  return {
    ok: false,
    reason: "below_minimum",
    minimumMinor: discountMinor + minimumChargedMinor(sellerBorneCostMinor, planKey),
    netMinor
  };
}

// Menor preço, sem desconto nem custo da loja, em que a loja recebe algo.
export function minimumChargeMinor(
  currency: MinimumPriceCurrency,
  planKey: AppSellPlanKey = DEFAULT_CHECKOUT_PLAN
): number {
  assertBRL(currency);
  return minimumChargedMinor(0, planKey);
}

function feeOf(charged: number, planKey: AppSellPlanKey): number {
  return charged > 0 ? directChargeApplicationFeeCents(charged, planKey) : 0;
}

function netOf(charged: number, borne: number, planKey: AppSellPlanKey): number {
  return charged - feeOf(charged, planKey) - borne;
}

// O líquido nunca cai quando o cobrado sobe um centavo (a parte variável da
// comissão sobe no máximo um centavo por centavo), então a busca binária acha
// o menor cobrado com líquido positivo sem varrer centavo por centavo.
function minimumChargedMinor(borne: number, planKey: AppSellPlanKey): number {
  let low = STRIPE_MINIMUM_CHARGE_BRL_MINOR;
  if (netOf(low, borne, planKey) > 0) return low;
  let high = low;
  while (netOf(high, borne, planKey) <= 0) {
    low = high;
    high *= 2;
  }
  // Invariante: netOf(low) <= 0 < netOf(high).
  while (high - low > 1) {
    const middle = low + Math.floor((high - low) / 2);
    if (netOf(middle, borne, planKey) > 0) {
      high = middle;
    } else {
      low = middle;
    }
  }
  return high;
}

function assertBRL(currency: string): void {
  if (currency !== "BRL") {
    throw new RangeError("minimum price is defined only for BRL");
  }
}

function assertMinor(name: string, value: number, min: number): void {
  if (!Number.isSafeInteger(value) || value < min || value > MAX_UNIT_AMOUNT_MINOR) {
    throw new RangeError(`${name} must be an integer between ${min} and ${MAX_UNIT_AMOUNT_MINOR}`);
  }
}
