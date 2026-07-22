// Client-safe (no "server-only") parsing for the two registration-checkout
// responses the checkout workspace component depends on, reached through the
// existing `/api/registration-checkout/...` proxy.
//
// UNVERIFIED ASSUMPTION — flag before enabling in staging/production: this
// repo has no local access to the Financial service source, a running
// instance, or the @autonom-ia/financial-sdk package (private, not installed
// here), so the exact quote/intent response shapes below are inferred from
// this codebase's own snake_case JSON conventions (see lib/demo-checkout.ts
// for the closest local precedent) and from universal Stripe key formats
// (`pk_`/`acct_` prefixes), not confirmed against the real Financial API.
// These two functions are the single place to update once confirmed. Refs #96.

export interface RegistrationCheckoutQuote {
  orderId: string;
  subtotalAmountMinor: number;
  discountAmountMinor: number;
  totalAmountMinor: number;
  currency: string;
}

export interface RegistrationCheckoutIntent {
  clientSecret: string;
  publishableKey: string;
  stripeAccountId: string;
}

const SAFE_REFERENCE = /^[A-Za-z0-9._:-]{1,128}$/;

export function parseRegistrationCheckoutQuote(value: unknown): RegistrationCheckoutQuote | null {
  if (!isRecord(value)) return null;
  const orderId = value.order_id;
  const subtotal = value.subtotal_amount_minor;
  const discount = value.discount_amount_minor;
  const total = value.total_amount_minor;
  const currency = value.currency;
  if (typeof orderId !== "string" || !SAFE_REFERENCE.test(orderId)) return null;
  if (!isMinorAmount(subtotal) || !isMinorAmount(discount) || !isMinorAmount(total)) return null;
  if (typeof currency !== "string" || !/^[A-Z]{3}$/.test(currency)) return null;
  return {
    orderId,
    subtotalAmountMinor: subtotal,
    discountAmountMinor: discount,
    totalAmountMinor: total,
    currency
  };
}

export function parseRegistrationCheckoutIntent(value: unknown): RegistrationCheckoutIntent | null {
  if (!isRecord(value)) return null;
  const clientSecret = value.client_secret;
  const publishableKey = value.publishable_key;
  const stripeAccountId = value.stripe_account_id;
  if (typeof clientSecret !== "string" || clientSecret.length < 8) return null;
  if (typeof publishableKey !== "string" || !publishableKey.startsWith("pk_")) return null;
  if (typeof stripeAccountId !== "string" || !stripeAccountId.startsWith("acct_")) return null;
  return { clientSecret, publishableKey, stripeAccountId };
}

function isMinorAmount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
