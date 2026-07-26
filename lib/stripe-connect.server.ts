import "server-only";

import { stripeClient } from "./stripe-config.server";

// Connect account creation for producers ("Conectar conta Autonom.ia").
//
// The controller configuration below is LOCKED. It was verified empirically
// against the live API (2026-07-25, probe accounts created and deleted): this
// exact combination is the only one that gives, at the same time,
//   - embedded onboarding in OUR page (requirement_collection "stripe" keeps
//     Stripe's KYC forms, PF and PJ, inside our UI via Connect embedded
//     components — a fully self-built KYC UI would FORCE the platform to
//     assume chargeback liability, which #143 rejects);
//   - no Stripe dashboard for the producer (stripe_dashboard "none" — the
//     producer never leaves our product);
//   - Stripe absorbing unrecoverable negative balances (losses "stripe" —
//     chargebacks still debit the producer's balance first under direct
//     charges, which is what "quem vendeu paga" means);
//   - the producer paying Stripe's own fees (fees "account").
// Do NOT change any of these fields without a new probe: several combinations
// are rejected by the API and the rejection rules are not all documented.
//
// `capabilities` is mandatory with dashboard "none": the platform is
// responsible for requesting what the account can do.

export async function createConnectedAccount() {
  return stripeClient().accounts.create({
    country: "BR",
    controller: {
      requirement_collection: "stripe",
      stripe_dashboard: { type: "none" },
      losses: { payments: "stripe" },
      fees: { payer: "account" }
    },
    capabilities: {
      card_payments: { requested: true },
      transfers: { requested: true }
    }
  });
}

// Single-use client secret consumed by the embedded onboarding component on
// the front-end (peça 3). Never stored, never logged.
export async function createOnboardingAccountSession(accountId: string) {
  const session = await stripeClient().accountSessions.create({
    account: accountId,
    components: { account_onboarding: { enabled: true } }
  });
  return session.client_secret;
}

// Cleanup for the create-then-bind failure window: if the account was created
// at the provider but the domain API refused (or failed) to bind it, an empty,
// never-onboarded account would otherwise be orphaned at the provider. Deleting
// an empty account is allowed and was exercised by the probes. Best-effort on
// purpose: the caller's error handling must not depend on the provider
// accepting the delete.
export async function deleteConnectedAccountBestEffort(accountId: string) {
  try {
    const deleted = await stripeClient().accounts.del(accountId);
    return deleted.deleted === true;
  } catch {
    return false;
  }
}
