import "server-only";

import Stripe from "stripe";

// Centralizes reading the Stripe credentials from env so no route ever inlines
// a key or logs one. The SAME code path serves TEST and LIVE: the only thing
// that changes is which APPSELL_STRIPE_SECRET_KEY is present in the environment
// (sk_test_… vs sk_live_…). No apiVersion is pinned here — the installed SDK
// (stripe@22.x) carries its own pinned default, which keeps request behavior
// deterministic without hardcoding a version string that could drift from the
// package.

export class StripeConfigError extends Error {}

// Accept both restricted (rk_) and standard (sk_) secret keys, test or live.
const SECRET_KEY = /^(sk|rk)_(test|live)_[A-Za-z0-9]+$/;
const WEBHOOK_SECRET = /^whsec_[A-Za-z0-9]+$/;

export type StripeKeyMode = "test" | "live";

let cachedKey = "";
let cachedClient: Stripe | null = null;

function readSecretKey(environment: Partial<Record<string, string | undefined>>): string {
  const secretKey = environment.APPSELL_STRIPE_SECRET_KEY ?? "";
  if (!SECRET_KEY.test(secretKey)) {
    // Never include the (partial) value in the message — only the var name.
    throw new StripeConfigError("invalid_stripe_secret_key");
  }
  return secretKey;
}

export function stripeClient(
  environment: Partial<Record<string, string | undefined>> = process.env
): Stripe {
  const secretKey = readSecretKey(environment);
  if (cachedClient && cachedKey === secretKey) return cachedClient;
  cachedClient = new Stripe(secretKey);
  cachedKey = secretKey;
  return cachedClient;
}

// Which Stripe mode the configured credential belongs to. The webhook route
// cross-checks this against `event.livemode` so a test-mode event can never
// grant access on a live deployment (and vice-versa) even if someone points a
// test webhook endpoint at the live URL. Derived from the key prefix, which
// Stripe guarantees; never from an env flag that could drift.
export function stripeKeyMode(
  environment: Partial<Record<string, string | undefined>> = process.env
): StripeKeyMode {
  const secretKey = readSecretKey(environment);
  return secretKey.startsWith("sk_live_") || secretKey.startsWith("rk_live_") ? "live" : "test";
}

export function stripeWebhookSecret(
  environment: Partial<Record<string, string | undefined>> = process.env
): string {
  const secret = environment.APPSELL_STRIPE_WEBHOOK_SECRET ?? "";
  if (!WEBHOOK_SECRET.test(secret)) {
    throw new StripeConfigError("invalid_stripe_webhook_secret");
  }
  return secret;
}

// Connect events (account.updated for connected accounts) arrive at their own
// endpoint with their own signing secret — Stripe scopes each webhook secret
// to one endpoint, and the platform endpoint's secret cannot verify them.
export function stripeConnectWebhookSecret(
  environment: Partial<Record<string, string | undefined>> = process.env
): string {
  const secret = environment.APPSELL_STRIPE_CONNECT_WEBHOOK_SECRET ?? "";
  if (!WEBHOOK_SECRET.test(secret)) {
    throw new StripeConfigError("invalid_stripe_connect_webhook_secret");
  }
  return secret;
}
