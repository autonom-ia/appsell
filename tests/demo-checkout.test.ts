import { expect, test } from "vitest";

import { parseDemoCheckoutEnvelope } from "../lib/demo-checkout";

const id = (suffix: number) => `91000000-0000-4000-8000-${String(suffix).padStart(12, "0")}`;

test("accepts the strict server-side quote contract", () => {
  expect(parseDemoCheckoutEnvelope({
    checkout: {
      schema_version: 1,
      seller: { id: id(1), slug: "autonomia-demo", name: "Autonom.ia Demo" },
      product: { id: id(2), slug: "codex-na-pratica", title: "Codex na prática" },
      offer: { id: id(3), unit_amount_minor: 9900, currency: "BRL" },
      already_owned: false,
      purchase_id: null,
      order_id: null,
      entitlement_id: null
    }
  })).toMatchObject({ already_owned: false, offer: { unit_amount_minor: 9900 } });
});

test("accepts zero total and rejects provider invocation or extra fields", () => {
  const checkout = {
    schema_version: 1,
    order_id: id(4),
    purchase_id: null,
    entitlement_id: null,
    subtotal_amount_minor: 9900,
    discount_amount_minor: 9900,
    total_amount_minor: 0,
    currency: "BRL",
    status: "processing",
    provider_called: false,
    already_owned: false,
    applied: true
  } as const;
  expect(parseDemoCheckoutEnvelope({ checkout })).toMatchObject({ total_amount_minor: 0 });
  expect(parseDemoCheckoutEnvelope({ checkout: { ...checkout, provider_called: true } })).toBeNull();
  expect(parseDemoCheckoutEnvelope({ checkout: { ...checkout, email: "buyer@example.test" } })).toBeNull();
});
