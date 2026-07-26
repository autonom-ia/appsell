import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  StripeConfigError,
  stripeConnectWebhookSecret
} from "../lib/stripe-config.server";

// Each Stripe endpoint has its own signing secret. Reusing the platform
// endpoint's secret on the Connect endpoint would let whoever holds one forge
// events for the other — the config layer refuses the reuse outright.

describe("stripeConnectWebhookSecret", () => {
  it("accepts a well-formed secret distinct from the platform one", () => {
    expect(
      stripeConnectWebhookSecret({
        APPSELL_STRIPE_CONNECT_WEBHOOK_SECRET: "whsec_connectplaceholder1",
        APPSELL_STRIPE_WEBHOOK_SECRET: "whsec_platformplaceholder1"
      })
    ).toBe("whsec_connectplaceholder1");
  });

  it("refuses the platform secret being reused for the Connect endpoint", () => {
    expect(() =>
      stripeConnectWebhookSecret({
        APPSELL_STRIPE_CONNECT_WEBHOOK_SECRET: "whsec_sameplaceholder1",
        APPSELL_STRIPE_WEBHOOK_SECRET: "whsec_sameplaceholder1"
      })
    ).toThrow(StripeConfigError);
  });

  it("refuses a malformed secret", () => {
    expect(() =>
      stripeConnectWebhookSecret({ APPSELL_STRIPE_CONNECT_WEBHOOK_SECRET: "not-a-secret" })
    ).toThrow(StripeConfigError);
  });
});
