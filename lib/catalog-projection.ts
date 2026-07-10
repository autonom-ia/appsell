export type ProductStatus = "draft" | "published" | "archived";

export interface CatalogProduct {
  id: string;
  sellerOrganizationId: string;
  sellerName: string;
  sellerSlug: string;
  slug: string;
  title: string;
  description: string;
  status: ProductStatus;
  priceInCents: number;
  accent: "blue" | "cyan" | "orange";
  lessonCount: number;
}
export interface Entitlement {
  buyerIdentitySub: string;
  sellerOrganizationId: string;
  productId: string;
  status: "active" | "revoked" | "expired";
  progressPercent: number;
}

export interface BuyerProduct extends CatalogProduct {
  progressPercent: number;
}

export function listSellerProducts(
  products: readonly CatalogProduct[],
  sellerOrganizationId: string,
): CatalogProduct[] {
  return products.filter((product) => product.sellerOrganizationId === sellerOrganizationId);
}

export function listBuyerProducts(
  products: readonly CatalogProduct[],
  entitlements: readonly Entitlement[],
  buyerIdentitySub: string,
): BuyerProduct[] {
  const entitled = new Map(
    entitlements
      .filter(
        (entitlement) =>
          entitlement.buyerIdentitySub === buyerIdentitySub && entitlement.status === "active",
      )
      .map((entitlement) => [
        `${entitlement.sellerOrganizationId}:${entitlement.productId}`,
        entitlement,
      ]),
  );

  return products.flatMap((product) => {
    if (product.status !== "published") return [];
    const entitlement = entitled.get(`${product.sellerOrganizationId}:${product.id}`);
    if (!entitlement) return [];
    return [{ ...product, progressPercent: entitlement.progressPercent }];
  });
}

export function listOtherPublishedProductsFromSeller(
  products: readonly CatalogProduct[],
  selected: Pick<CatalogProduct, "id" | "sellerOrganizationId">,
): CatalogProduct[] {
  return products.filter(
    (product) =>
      product.sellerOrganizationId === selected.sellerOrganizationId &&
      product.id !== selected.id &&
      product.status === "published",
  );
}
