import { APPSELL_PRICING_PLANS, type AppSellPlanKey } from "./appsell-pricing";

// The platform's commission on a direct charge, in cents, computed SERVER-SIDE
// from the pricing contract — never from anything the browser sent. Rounding is
// floor_per_transaction (APPSELL_VARIABLE_FEE_ROUNDING): the variable part is
// floored on each transaction, in the seller's favor.
//
// Every seller charges under "launch" until plan selection ships with trilho A
// (#142): highest take rate, no monthly — the safe default for a seller that
// never chose a paid plan.
export const DEFAULT_CHECKOUT_PLAN: AppSellPlanKey = "launch";

export function directChargeApplicationFeeCents(
  amountCents: number,
  planKey: AppSellPlanKey = DEFAULT_CHECKOUT_PLAN
): number {
  if (!Number.isInteger(amountCents) || amountCents <= 0) {
    throw new RangeError("application fee requires a positive integer amount");
  }
  const plan = APPSELL_PRICING_PLANS[planKey];
  const variable = Math.floor((amountCents * plan.platformRateBps) / 10_000);
  // Stripe refuses application_fee_amount > amount. On a price small enough
  // that the fixed fee exceeds it, the commission is capped at the full amount
  // (the seller nets zero) rather than failing the buyer's checkout.
  return Math.min(variable + plan.platformFixedFeeCents, amountCents);
}
