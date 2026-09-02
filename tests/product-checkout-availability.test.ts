import { afterEach, expect, test, vi } from "vitest";

vi.mock("server-only", () => ({}));

import { isProductCheckoutAvailable } from "../lib/product-checkout-availability.server";

const originalEnvironment = { ...process.env };

afterEach(() => {
  process.env = { ...originalEnvironment };
});

test("permits Financial checkout without an interim product map", () => {
  process.env.APPSELL_CHECKOUT_PROVIDER = "financial_checkout";
  process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = "{}";

  expect(isProductCheckoutAvailable("hub2you", "ia-na-pratica")).toBe(true);
});

test("keeps local Stripe checkout unavailable without a product map", () => {
  process.env.APPSELL_CHECKOUT_PROVIDER = "local_stripe";
  process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = "{}";

  expect(isProductCheckoutAvailable("hub2you", "ia-na-pratica")).toBe(false);
});

test("permits local Stripe checkout when the product is mapped", () => {
  process.env.APPSELL_CHECKOUT_PROVIDER = "local_stripe";
  process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON = JSON.stringify({
    "hub2you/ia-na-pratica": {
      financial_product_key: "prod_ia_na_pratica_v1"
    }
  });

  expect(isProductCheckoutAvailable("hub2you", "ia-na-pratica")).toBe(true);
});
