import "server-only";

import Stripe from "stripe";

// Centralizes reading the Stripe credentials from env so no route ever inlines
// a key or logs one. The SAME code path serves TEST and LIVE: the only thing
// that changes is which STRIPE_SECRET_KEY is present in the environment
// (sk_test_… vs sk_live_…). No apiVersion is pinned here — the installed SDK
// (stripe@22.x) carries its own pinned default, which keeps request behavior
// deterministic without hardcoding a version string that could drift from the
// package.

export class StripeConfigError extends Error {}

// Accept both restricted (rk_) and standard (sk_) secret keys, test or live.
const SECRET_KEY = /^(sk|rk)_(test|live)_[A-Za-z0-9]+$/;
const WEBHOOK_SECRET = /^whsec_[A-Za-z0-9]+$/;

let cachedKey = "";
let cachedClient: Stripe | null = null;

export function stripeClient(
  environment: Partial<Record<string, string | undefined>> = process.env
): Stripe {
  const secretKey = environment.STRIPE_SECRET_KEY ?? "";
  if (!SECRET_KEY.test(secretKey)) {
    // Never include the (partial) value in the message — only the var name.
    throw new StripeConfigError("invalid_stripe_secret_key");
  }
  if (cachedClient && cachedKey === secretKey) return cachedClient;
  cachedClient = new Stripe(secretKey);
  cachedKey = secretKey;
  return cachedClient;
}

export function stripeWebhookSecret(
  environment: Partial<Record<string, string | undefined>> = process.env
): string {
  const secret = environment.STRIPE_WEBHOOK_SECRET ?? "";
  if (!WEBHOOK_SECRET.test(secret)) {
    throw new StripeConfigError("invalid_stripe_webhook_secret");
  }
  return secret;
}
