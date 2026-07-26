import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  StripeConfigError,
  stripeConnectWebhookSecret
} from "../lib/stripe-config.server";

// Each Stripe endpoint has its own signing secret. Reusing the platform
// endpoint's secret on the Connect endpoint would let whoever holds one forge
// events for the other — the config layer refuses the reuse outright.
//
// The fixtures are BUILT, never written as literals next to a *_SECRET key: a
// quoted value in that position reads as a real credential to secret scanners
// (gitleaks' generic-api-key rule flags exactly that shape), and a test fixture
// must never look like one. `whsec_<label>` still satisfies the config's
// /^whsec_[A-Za-z0-9]+$/ contract while being unmistakably fake.
const fakeWebhookSecret = (label: string) => `whsec_${label}`;

describe("stripeConnectWebhookSecret", () => {
  it("accepts a well-formed secret distinct from the platform one", () => {
    const connect = fakeWebhookSecret("connect");
    expect(
      stripeConnectWebhookSecret({
        APPSELL_STRIPE_CONNECT_WEBHOOK_SECRET: connect,
        APPSELL_STRIPE_WEBHOOK_SECRET: fakeWebhookSecret("platform")
      })
    ).toBe(connect);
  });

  it("refuses the platform secret being reused for the Connect endpoint", () => {
    const shared = fakeWebhookSecret("shared");
    expect(() =>
      stripeConnectWebhookSecret({
        APPSELL_STRIPE_CONNECT_WEBHOOK_SECRET: shared,
        APPSELL_STRIPE_WEBHOOK_SECRET: shared
      })
    ).toThrow(StripeConfigError);
  });

  it("refuses a malformed secret", () => {
    expect(() =>
      stripeConnectWebhookSecret({ APPSELL_STRIPE_CONNECT_WEBHOOK_SECRET: "not-a-secret" })
    ).toThrow(StripeConfigError);
  });
});
