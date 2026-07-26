import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { PAYOUT_STAGE_COPY, payoutStageFrom } from "../lib/payout-status";

// A tela inteira deriva do estágio; um estágio errado mostra ao produtor uma
// promessa falsa sobre o dinheiro dele ("pronta para receber" sem estar).

describe("payoutStageFrom", () => {
  it("treats no binding as not connected", () => {
    expect(payoutStageFrom(null)).toBe("not_connected");
  });

  it("only calls the account active when it can charge AND pay out", () => {
    expect(
      payoutStageFrom({
        charges_enabled: true,
        payouts_enabled: true,
        details_submitted: true,
        disabled_reason: null
      })
    ).toBe("active");
    expect(
      payoutStageFrom({
        charges_enabled: true,
        payouts_enabled: false,
        details_submitted: true,
        disabled_reason: null
      })
    ).toBe("under_review");
  });

  it("keeps an incomplete verification resumable", () => {
    expect(
      payoutStageFrom({
        charges_enabled: false,
        payouts_enabled: false,
        details_submitted: false,
        disabled_reason: null
      })
    ).toBe("verification_pending");
  });

  it("surfaces a provider block as action required, even mid-verification", () => {
    expect(
      payoutStageFrom({
        charges_enabled: false,
        payouts_enabled: false,
        details_submitted: false,
        disabled_reason: "requirements.past_due"
      })
    ).toBe("action_required");
  });

  it("shows under review after submission while the provider decides", () => {
    expect(
      payoutStageFrom({
        charges_enabled: false,
        payouts_enabled: false,
        details_submitted: true,
        disabled_reason: null
      })
    ).toBe("under_review");
  });

  it("never names the payment gateway in producer-facing copy", () => {
    const copyText = Object.values(PAYOUT_STAGE_COPY)
      .flatMap((entry) => [entry.badge, entry.title, entry.description, entry.action ?? ""])
      .join(" ");
    expect(copyText.toLowerCase()).not.toContain("stripe");
  });

  it("never names the gateway in the connect card's own strings either", () => {
    // The stage dictionary is only half the surface: the card hardcodes error
    // messages, headings and the footnote. Auditing the SOURCE (minus its
    // imports and code comments, which legitimately reference the SDK) is what
    // keeps a future string edit from leaking the gateway name to producers.
    const source = readFileSync(
      join(__dirname, "..", "app", "components", "payout-connect-card.tsx"),
      "utf8"
    );
    const withoutCodeReferences = source
      .split("\n")
      .filter((line) => {
        const trimmed = line.trim();
        return !trimmed.startsWith("//") && !trimmed.startsWith("*");
      })
      .join("\n")
      // Legitimate CODE references, none of them user-visible: package
      // specifiers, the env var name, and PascalCase SDK identifiers
      // (StripeConnectInstance, ...). A leak into copy would be the word
      // followed by space/punctuation, which none of these patterns match.
      .replace(/@stripe\/[a-z-]+/g, "")
      .replace(/APPSELL_STRIPE_[A-Z_]+/g, "")
      .replace(/\bStripe(?=[A-Z])[A-Za-z]*/g, "");
    expect(withoutCodeReferences).not.toMatch(/stripe/i);
  });

  it("offers an action exactly when the producer can act", () => {
    expect(PAYOUT_STAGE_COPY.not_connected.action).toBe("Conectar conta Autonom.ia");
    expect(PAYOUT_STAGE_COPY.verification_pending.action).not.toBeNull();
    expect(PAYOUT_STAGE_COPY.action_required.action).not.toBeNull();
    expect(PAYOUT_STAGE_COPY.under_review.action).toBeNull();
    expect(PAYOUT_STAGE_COPY.active.action).toBeNull();
  });
});
