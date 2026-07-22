import { expect, test } from "vitest";

import { isPendingRegistrationCheckoutOrder, parsePaidRegistrationCheckoutOrder } from "../lib/checkout-order";

function paidOrder() {
  return {
    order_id: "order-123",
    status: "paid",
    financial_product_key: "prod_codex_v1",
    buyer_email: "  Buyer@Example.TEST  ",
    paid_at: "2026-07-21T10:00:00.000Z",
    // Third-party response: extra fields the Financial order object very
    // likely carries (amount, provider ids, ...) must be tolerated, not
    // rejected — this contract is not one we control end to end.
    amount_minor: 9900,
    provider: "stripe"
  };
}

test("accepts a paid order and normalizes e-mail and occurred_at", () => {
  const facts = parsePaidRegistrationCheckoutOrder("order-123", paidOrder());
  expect(facts).toEqual({
    orderId: "order-123",
    financialProductKey: "prod_codex_v1",
    buyerEmail: "buyer@example.test",
    occurredAt: "2026-07-21T10:00:00.000Z"
  });
});

test("accepts the 'succeeded' paid status too, case-insensitively", () => {
  const facts = parsePaidRegistrationCheckoutOrder("order-123", { ...paidOrder(), status: "SUCCEEDED" });
  expect(facts?.orderId).toBe("order-123");
});

test("rejects an order id that does not match the one requested", () => {
  expect(parsePaidRegistrationCheckoutOrder("order-999", paidOrder())).toBeNull();
});

test("rejects a non-paid status", () => {
  expect(parsePaidRegistrationCheckoutOrder("order-123", { ...paidOrder(), status: "processing" })).toBeNull();
});

test("rejects a missing or unsafe financial_product_key, malformed e-mail, or unparsable paid_at", () => {
  expect(parsePaidRegistrationCheckoutOrder("order-123", { ...paidOrder(), financial_product_key: "has space" })).toBeNull();
  expect(parsePaidRegistrationCheckoutOrder("order-123", { ...paidOrder(), buyer_email: "not-an-email" })).toBeNull();
  const longLocal = "a".repeat(240) + "@x.co"; // 245 bytes > 242 do ingress
  expect(parsePaidRegistrationCheckoutOrder("order-123", { ...paidOrder(), buyer_email: longLocal })).toBeNull();
  expect(parsePaidRegistrationCheckoutOrder("order-123", { ...paidOrder(), paid_at: "not-a-date" })).toBeNull();
  const missingPaidAt = paidOrder() as Record<string, unknown>;
  delete missingPaidAt.paid_at;
  expect(parsePaidRegistrationCheckoutOrder("order-123", missingPaidAt)).toBeNull();
});

test("rejects a non-object value", () => {
  expect(parsePaidRegistrationCheckoutOrder("order-123", null)).toBeNull();
  expect(parsePaidRegistrationCheckoutOrder("order-123", "order-123")).toBeNull();
  expect(parsePaidRegistrationCheckoutOrder("order-123", [paidOrder()])).toBeNull();
});

test("isPendingRegistrationCheckoutOrder is true only for a matching order id with a non-paid status", () => {
  expect(isPendingRegistrationCheckoutOrder("order-123", { order_id: "order-123", status: "processing" })).toBe(true);
  expect(isPendingRegistrationCheckoutOrder("order-123", { order_id: "order-123", status: "paid" })).toBe(false);
  expect(isPendingRegistrationCheckoutOrder("order-123", { order_id: "order-999", status: "processing" })).toBe(false);
  expect(isPendingRegistrationCheckoutOrder("order-123", null)).toBe(false);
});
