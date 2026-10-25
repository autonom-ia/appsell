import { describe, expect, it } from "vitest";

import {
  CONNECT_IDEMPOTENCY_WINDOW_MS,
  connectCheckoutIdempotencyKey,
  isFinancialConnectSeller
} from "../lib/financial-connect-checkout";

const SELLER_ID = "11111111-1111-4111-8111-111111111111";

const parts = {
  sellerId: SELLER_ID,
  productId: "22222222-2222-4222-8222-222222222222",
  offerId: "33333333-3333-4333-8333-333333333333",
  buyerIdentitySub: "cognito-sub-default",
  amountMinor: 4990,
  accountId: "acct_store_a",
  applicationFeeAmountMinor: 323
};

describe("isFinancialConnectSeller", () => {
  it("is off when the list is unset or empty", () => {
    expect(isFinancialConnectSeller(SELLER_ID, {})).toBe(false);
    expect(isFinancialConnectSeller(SELLER_ID, { APPSELL_FINANCIAL_CONNECT_SELLER_IDS: "" })).toBe(false);
    expect(isFinancialConnectSeller(SELLER_ID, { APPSELL_FINANCIAL_CONNECT_SELLER_IDS: " , ," })).toBe(false);
  });

  it("matches only an exact listed id, ignoring spaces and case", () => {
    const environment = { APPSELL_FINANCIAL_CONNECT_SELLER_IDS: ` ${SELLER_ID.toUpperCase()} ,other` };
    expect(isFinancialConnectSeller(SELLER_ID, environment)).toBe(true);
    expect(isFinancialConnectSeller(SELLER_ID.slice(0, 8), environment)).toBe(false);
    expect(isFinancialConnectSeller("66666666-6666-4666-8666-666666666666", environment)).toBe(false);
  });
});

describe("connectCheckoutIdempotencyKey", () => {
  const start = Date.UTC(2026, 9, 25, 12, 0, 0);

  it("is stable inside one window and changes in the next", () => {
    const key = connectCheckoutIdempotencyKey(parts, start);
    expect(connectCheckoutIdempotencyKey(parts, start + CONNECT_IDEMPOTENCY_WINDOW_MS - 1)).toBe(key);
    expect(connectCheckoutIdempotencyKey(parts, start + CONNECT_IDEMPOTENCY_WINDOW_MS)).not.toBe(key);
  });

  it("changes with every part of the order", () => {
    const key = connectCheckoutIdempotencyKey(parts, start);
    const variants = [
      { sellerId: "66666666-6666-4666-8666-666666666666" },
      { productId: "77777777-7777-4777-8777-777777777777" },
      { offerId: "88888888-8888-4888-8888-888888888888" },
      { buyerIdentitySub: "cognito-sub-other" },
      { amountMinor: 5990 },
      { accountId: "acct_store_b" },
      { applicationFeeAmountMinor: 324 }
    ];
    for (const variant of variants) {
      expect(connectCheckoutIdempotencyKey({ ...parts, ...variant }, start)).not.toBe(key);
    }
  });

  it("fits the Financial limit and hides the buyer", () => {
    const key = connectCheckoutIdempotencyKey(parts, start);
    expect(key.startsWith("sell-connect-v1:")).toBe(true);
    expect(key.length).toBeLessThanOrEqual(160);
    expect(key).not.toContain(parts.buyerIdentitySub);
  });
});
