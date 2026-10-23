import { expect, test } from "vitest";

import {
  checkoutProductMapIsValid,
  lookupCheckoutProductByKey,
  parseCheckoutProductMap,
  resolveCheckoutProduct
} from "../lib/checkout-config";

test("parses a strict product map with one or more entries", () => {
  const raw = JSON.stringify({
    "autonomia-demo/codex-na-pratica": { financial_product_key: "prod_codex_v1" },
    "outra-loja/outro-produto": { financial_product_key: "prod_outro_v1" }
  });
  const map = parseCheckoutProductMap(raw);
  expect(map).not.toBeNull();
  expect(map?.get("autonomia-demo/codex-na-pratica")).toEqual({ financialProductKey: "prod_codex_v1" });
  expect(map?.get("outra-loja/outro-produto")).toEqual({ financialProductKey: "prod_outro_v1" });
});

test("treats missing or empty env as an empty map, not an error", () => {
  expect(parseCheckoutProductMap(undefined)).toEqual(new Map());
  expect(parseCheckoutProductMap("")).toEqual(new Map());
  expect(parseCheckoutProductMap("{}")).toEqual(new Map());
});

test("rejects malformed JSON", () => {
  expect(parseCheckoutProductMap("{not json")).toBeNull();
});

test("rejects a non-object top level value", () => {
  expect(parseCheckoutProductMap("[]")).toBeNull();
  expect(parseCheckoutProductMap('"a string"')).toBeNull();
  expect(parseCheckoutProductMap("42")).toBeNull();
});

test("rejects keys that are not <sellerSlug>/<productSlug>", () => {
  for (const key of ["nosep", "/leading-slash", "trailing-slash/", "Upper/case", "a//b", "a/b/c"]) {
    expect(parseCheckoutProductMap(JSON.stringify({ [key]: { financial_product_key: "prod_x" } }))).toBeNull();
  }
});

test("rejects an entry missing financial_product_key or with extra fields", () => {
  expect(parseCheckoutProductMap(JSON.stringify({ "a/b": {} }))).toBeNull();
  expect(parseCheckoutProductMap(JSON.stringify({
    "a/b": { financial_product_key: "prod_x", extra: 1 }
  }))).toBeNull();
});

test("rejects a non-string or unsafe financial_product_key", () => {
  expect(parseCheckoutProductMap(JSON.stringify({ "a/b": { financial_product_key: 123 } }))).toBeNull();
  expect(parseCheckoutProductMap(JSON.stringify({ "a/b": { financial_product_key: "" } }))).toBeNull();
  expect(parseCheckoutProductMap(JSON.stringify({ "a/b": { financial_product_key: "has space" } }))).toBeNull();
});

test("resolveCheckoutProduct reads the configured env var and returns null for unknown products", () => {
  const original = process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON;
  process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = JSON.stringify({
    "autonomia-demo/codex-na-pratica": { financial_product_key: "prod_codex_v1" }
  });
  try {
    expect(resolveCheckoutProduct("autonomia-demo", "codex-na-pratica")).toEqual({ financialProductKey: "prod_codex_v1" });
    expect(resolveCheckoutProduct("autonomia-demo", "unknown-product")).toBeNull();
    expect(resolveCheckoutProduct("outra-loja", "codex-na-pratica")).toBeNull();
  } finally {
    if (original === undefined) delete process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON;
    else process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = original;
  }
});

test("resolveCheckoutProduct fails closed (null) when the env var is malformed", () => {
  const original = process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON;
  process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = "{not json";
  try {
    expect(resolveCheckoutProduct("autonomia-demo", "codex-na-pratica")).toBeNull();
  } finally {
    if (original === undefined) delete process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON;
    else process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = original;
  }
});

test("lookupCheckoutProductByKey says why a key did not resolve to exactly one product", () => {
  const original = process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON;
  try {
    process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = JSON.stringify({
      "loja-a/curso": { financial_product_key: "prod_a" },
      "loja-b/curso": { financial_product_key: "prod_dup" },
      "loja-c/curso": { financial_product_key: "prod_dup" }
    });
    expect(lookupCheckoutProductByKey("prod_a")).toEqual({ kind: "found", sellerSlug: "loja-a", productSlug: "curso" });
    expect(lookupCheckoutProductByKey("prod_missing")).toEqual({ kind: "not_mapped" });
    expect(lookupCheckoutProductByKey("prod_dup")).toEqual({ kind: "ambiguous" });

    process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = "{not json";
    expect(lookupCheckoutProductByKey("prod_a")).toEqual({ kind: "map_invalid" });
  } finally {
    if (original === undefined) delete process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON;
    else process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = original;
  }
});

test("checkoutProductMapIsValid accepts an absent or well-formed map and refuses one that does not parse", () => {
  expect(checkoutProductMapIsValid({})).toBe(true);
  expect(checkoutProductMapIsValid({ APPSELL_CHECKOUT_PRODUCT_MAP_JSON: "" })).toBe(true);
  expect(checkoutProductMapIsValid({
    APPSELL_CHECKOUT_PRODUCT_MAP_JSON: JSON.stringify({ "a/b": { financial_product_key: "prod_x" } })
  })).toBe(true);
  expect(checkoutProductMapIsValid({ APPSELL_CHECKOUT_PRODUCT_MAP_JSON: "{not json" })).toBe(false);
  expect(checkoutProductMapIsValid({ APPSELL_CHECKOUT_PRODUCT_MAP_JSON: JSON.stringify({ "a/b": {} }) })).toBe(false);
});
