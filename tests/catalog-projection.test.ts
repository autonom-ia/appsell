import { describe, expect, it } from "vitest";
import { demoEntitlements, demoProducts, DEMO_BUYER_SUB, DEMO_SELLER_ID } from "../lib/demo-catalog";
import { listBuyerProducts, listOtherPublishedProductsFromSeller, listSellerProducts } from "../lib/catalog-projection";
import { canAccessDemoPreview } from "../lib/demo-view";

describe("catalog projections", () => {
  it("never leaks products from another seller into the seller catalog", () => {
    const products = listSellerProducts(demoProducts, DEMO_SELLER_ID);
    expect(products).not.toHaveLength(0);
    expect(products.every((product) => product.sellerOrganizationId === DEMO_SELLER_ID)).toBe(true);
  });

  it("returns only active, published products entitled to the authenticated buyer", () => {
    const products = listBuyerProducts(demoProducts, demoEntitlements, DEMO_BUYER_SUB);
    expect(products.map((product) => product.id)).toEqual(["product-creator-os", "product-growth-systems"]);
    expect(products.every((product) => product.status === "published")).toBe(true);
    expect(listBuyerProducts(demoProducts, demoEntitlements, "another-buyer")).toEqual([]);
  });

  it("never recommends drafts or products from another seller", () => {
    const selected = demoProducts[0]!;
    const others = listOtherPublishedProductsFromSeller(demoProducts, selected);
    expect(others.map((product) => product.id)).toEqual(["product-ai-playbook"]);
    expect(others.every((product) => product.sellerOrganizationId === selected.sellerOrganizationId)).toBe(true);
  });

  it("fails the demo preview closed for production and unknown subjects", () => {
    expect(canAccessDemoPreview("dev-user-001", "development")).toBe(true);
    expect(canAccessDemoPreview("another-subject", "development")).toBe(false);
    expect(canAccessDemoPreview("dev-user-001", "production")).toBe(false);
    expect(canAccessDemoPreview(undefined, "development")).toBe(false);
  });
});
