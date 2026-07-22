import { expect, test } from "vitest";

import { parseRegistrationCheckoutIntent, parseRegistrationCheckoutQuote } from "../lib/checkout-workspace-contract";

test("accepts a well-formed quote and rejects malformed amounts/currency", () => {
  const quote = {
    order_id: "order-123",
    subtotal_amount_minor: 9900,
    discount_amount_minor: 0,
    total_amount_minor: 9900,
    currency: "BRL"
  };
  expect(parseRegistrationCheckoutQuote(quote)).toEqual({
    orderId: "order-123",
    subtotalAmountMinor: 9900,
    discountAmountMinor: 0,
    totalAmountMinor: 9900,
    currency: "BRL"
  });
  expect(parseRegistrationCheckoutQuote({ ...quote, total_amount_minor: -1 })).toBeNull();
  expect(parseRegistrationCheckoutQuote({ ...quote, total_amount_minor: 1.5 })).toBeNull();
  expect(parseRegistrationCheckoutQuote({ ...quote, currency: "brl" })).toBeNull();
  expect(parseRegistrationCheckoutQuote({ ...quote, order_id: "" })).toBeNull();
  expect(parseRegistrationCheckoutQuote(null)).toBeNull();
});

test("accepts a well-formed intent and enforces real Stripe key prefixes", () => {
  const intent = {
    client_secret: "pi_123_secret_abcdef",
    publishable_key: "pk_test_abc123",
    stripe_account_id: "acct_1AbcDef"
  };
  expect(parseRegistrationCheckoutIntent(intent)).toEqual({
    clientSecret: "pi_123_secret_abcdef",
    publishableKey: "pk_test_abc123",
    stripeAccountId: "acct_1AbcDef"
  });
  expect(parseRegistrationCheckoutIntent({ ...intent, publishable_key: "sk_test_abc123" })).toBeNull();
  expect(parseRegistrationCheckoutIntent({ ...intent, stripe_account_id: "not-an-account" })).toBeNull();
  expect(parseRegistrationCheckoutIntent({ ...intent, client_secret: "short" })).toBeNull();
  expect(parseRegistrationCheckoutIntent(undefined)).toBeNull();
});
