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
      publication_version: 1
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
});
