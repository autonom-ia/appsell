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

  it("offers an action exactly when the producer can act", () => {
    expect(PAYOUT_STAGE_COPY.not_connected.action).toBe("Conectar conta Autonom.ia");
    expect(PAYOUT_STAGE_COPY.verification_pending.action).not.toBeNull();
    expect(PAYOUT_STAGE_COPY.action_required.action).not.toBeNull();
    expect(PAYOUT_STAGE_COPY.under_review.action).toBeNull();
    expect(PAYOUT_STAGE_COPY.active.action).toBeNull();
  });
});
