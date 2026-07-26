import { describe, expect, it } from "vitest";

import { directChargeApplicationFeeCents } from "../lib/checkout-fee";

// The commission is money arithmetic: a wrong rounding direction or an
// uncapped fee either shortchanges the seller or fails the buyer's checkout.

describe("directChargeApplicationFeeCents", () => {
  it("charges launch pricing by default: floor(bps) + fixed", () => {
    // R$1,00 on launch (550bps + R$0,49): floor(100*550/10000)=5 + 49 = 54.
    expect(directChargeApplicationFeeCents(100)).toBe(54);
    // R$199,00: floor(19900*550/10000)=1094 + 49 = 1143.
    expect(directChargeApplicationFeeCents(19_900)).toBe(1_143);
  });

  it("floors the variable part per transaction, in the seller's favor", () => {
    // 999 * 550 / 10000 = 54.945 -> 54, never 55.
    expect(directChargeApplicationFeeCents(999)).toBe(54 + 49);
  });

  it("applies the chosen plan's rate", () => {
    expect(directChargeApplicationFeeCents(19_900, "scale")).toBe(
      Math.floor((19_900 * 190) / 10_000) + 29
    );
  });

  it("caps the fee at the charged amount", () => {
    // A price below the fixed fee cannot produce a fee above the charge.
    expect(directChargeApplicationFeeCents(30)).toBe(30);
  });

  it("refuses a non-positive or fractional amount", () => {
    expect(() => directChargeApplicationFeeCents(0)).toThrow(RangeError);
    expect(() => directChargeApplicationFeeCents(-100)).toThrow(RangeError);
    expect(() => directChargeApplicationFeeCents(10.5)).toThrow(RangeError);
  });
});
