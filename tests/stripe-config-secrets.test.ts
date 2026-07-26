import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  StripeConfigError,
  stripeConnectWebhookSecret,
  stripePublishableKey
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

// Built, not literal — same reason as the webhook fixtures above.
const fakeSecretKey = (mode: "test" | "live") => `sk_${mode}_${mode}secretplaceholder`;
const fakePublishableKey = (mode: "test" | "live") => `pk_${mode}_${mode}pubplaceholder`;

describe("stripePublishableKey", () => {
  it("returns the key when its mode matches the secret key's", () => {
    for (const mode of ["test", "live"] as const) {
      expect(
        stripePublishableKey({
          APPSELL_STRIPE_PUBLISHABLE_KEY: fakePublishableKey(mode),
          APPSELL_STRIPE_SECRET_KEY: fakeSecretKey(mode)
        })
      ).toBe(fakePublishableKey(mode));
    }
  });

  it("refuses a publishable key from the other mode", () => {
    // A test key with a live secret fails inside the provider's frame with an
    // opaque message, in front of the producer. It has to fail here instead.
    for (const [pub, secret] of [
      ["test", "live"],
      ["live", "test"]
    ] as const) {
      expect(() =>
        stripePublishableKey({
          APPSELL_STRIPE_PUBLISHABLE_KEY: fakePublishableKey(pub),
          APPSELL_STRIPE_SECRET_KEY: fakeSecretKey(secret)
        })
      ).toThrow(StripeConfigError);
    }
  });

  it("refuses a secret key handed in by mistake, and a missing key", () => {
    // Pasting the secret into the publishable slot would ship it to every
    // browser. The prefix check is what stops that.
    expect(() =>
      stripePublishableKey({
        APPSELL_STRIPE_PUBLISHABLE_KEY: fakeSecretKey("live"),
        APPSELL_STRIPE_SECRET_KEY: fakeSecretKey("live")
      })
    ).toThrow(StripeConfigError);
    expect(() =>
      stripePublishableKey({ APPSELL_STRIPE_SECRET_KEY: fakeSecretKey("live") })
    ).toThrow(StripeConfigError);
  });

  it("refuses when there is no secret key to compare the mode against", () => {
    expect(() =>
      stripePublishableKey({ APPSELL_STRIPE_PUBLISHABLE_KEY: fakePublishableKey("live") })
    ).toThrow(StripeConfigError);
  });
});
