import { randomUUID } from "node:crypto";

import { describe, expect, it } from "vitest";

import { parsePublicStorefront } from "../lib/public-storefront";

function fixture() {
  return {
    schema_version: 1,
    seller: { id: randomUUID(), slug: "autonomia-demo", name: "Autonom.ia Demo" },
    products: [{
      id: randomUUID(),
      slug: "codex-na-pratica",
      title: "Codex na prática",
      description: "Curso demonstrativo.",
      publication_id: randomUUID(),
      publication_version: 1,
      price: null
    }]
  };
}

describe("public storefront payload", () => {
  it("accepts one strict published catalog", () => {
    const value = fixture();
    expect(parsePublicStorefront(value)).toEqual(value);
  });

  it("accepts an empty published catalog", () => {
    const value = { ...fixture(), products: [] };
    expect(parsePublicStorefront(value)).toEqual(value);
  });

  it("rejects duplicate products and extra fields", () => {
    const value = fixture();
    expect(parsePublicStorefront({ ...value, products: [value.products[0], value.products[0]] }))
      .toBeNull();
    expect(parsePublicStorefront({ ...value, checkout_url: "https://example.test" })).toBeNull();
  });

  it("rejects malformed seller and publication data", () => {
    const value = fixture();
    expect(parsePublicStorefront({ ...value, seller: { ...value.seller, slug: "../admin" } }))
      .toBeNull();
    expect(parsePublicStorefront({
      ...value,
      products: [{ ...value.products[0], publication_version: 0 }]
    })).toBeNull();
  });

  it("accepts a live-priced product, a pending (null) price, and rejects malformed price", () => {
    const value = fixture();
    const priced = {
      ...value,
      products: [{
        ...value.products[0],
        price: { unit_amount_minor: 49900, currency: "BRL", billing_period: "monthly" as const }
      }]
    };
    expect(parsePublicStorefront(priced)).toEqual(priced);

    // Unpriced offers surface `price: null` (pending), which is accepted.
    expect(parsePublicStorefront(value)).toEqual(value);

    const missingPrice = { ...value.products[0] } as Record<string, unknown>;
    delete missingPrice.price;
    expect(parsePublicStorefront({ ...value, products: [missingPrice] })).toBeNull();

    for (const price of [
      { unit_amount_minor: -1, currency: "BRL", billing_period: "one_time" },
      { unit_amount_minor: 100_000_000_000, currency: "BRL", billing_period: "one_time" },
      { unit_amount_minor: 1.5, currency: "BRL", billing_period: "one_time" },
      { unit_amount_minor: 1000, currency: "brl", billing_period: "one_time" },
      { unit_amount_minor: 1000, currency: "BRLL", billing_period: "one_time" },
      { unit_amount_minor: 1000, currency: "BRL", billing_period: "weekly" },
      { unit_amount_minor: 1000, currency: "BRL", billing_period: "one_time", extra: 1 }
    ]) {
      expect(parsePublicStorefront({
        ...value,
        products: [{ ...value.products[0], price }]
      })).toBeNull();
    }
  });

  it("accepts the explicit free and paid access modes", () => {
    const value = fixture();
    const free = { ...value, products: [{ ...value.products[0], access_mode: "free" as const }] };
    const paid = {
      ...value,
      products: [{
        ...value.products[0],
        access_mode: "paid" as const,
        price: { unit_amount_minor: 4700, currency: "BRL", billing_period: "one_time" as const }
      }]
    };
    expect(parsePublicStorefront(free)).toEqual(free);
    expect(parsePublicStorefront(paid)).toEqual(paid);
    expect(parsePublicStorefront({
      ...value,
      products: [{ ...value.products[0], access_mode: "unknown" }]
    })).toBeNull();
  });
});
