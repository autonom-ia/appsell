export interface BuyerProductItem {
  entitlement_id: string;
  resume_lesson_id: string | null;
  seller: { id: string; slug: string; name: string };
  product: {
    id: string;
    slug: string;
    title: string;
    description: string;
    publication_id: string;
    publication_version: number;
  };
  progress_percent: number;
}

export interface BuyerSellerProductItem {
  seller: BuyerProductItem["seller"];
  product: BuyerProductItem["product"];
}

export function parseBuyerProductsPayload(value: unknown): { items: BuyerProductItem[] } | null {
  if (!hasExactKeys(value, ["items"]) || !Array.isArray(value.items)) return null;
  const entitlements = new Set<string>();
  const products = new Set<string>();
  for (const item of value.items) {
    if (!isBuyerProductItem(item) || entitlements.has(item.entitlement_id)) return null;
    const productKey = `${item.seller.id}:${item.product.id}`;
    if (products.has(productKey)) return null;
    entitlements.add(item.entitlement_id);
    products.add(productKey);
  }
  return { items: value.items };
}

export function parseBuyerSellerProductsPayload(
  value: unknown
): { items: BuyerSellerProductItem[] } | null {
  if (!hasExactKeys(value, ["items"]) || !Array.isArray(value.items)) return null;
  const products = new Set<string>();
  let sellerId: string | null = null;
  for (const item of value.items) {
    if (!hasExactKeys(item, ["seller", "product"]) ||
      !isSeller(item.seller) || !isProduct(item.product)) return null;
    if (sellerId !== null && item.seller.id !== sellerId) return null;
    if (products.has(item.product.id)) return null;
    sellerId = item.seller.id;
    products.add(item.product.id);
  }
  return { items: value.items } as { items: BuyerSellerProductItem[] };
}

function isBuyerProductItem(value: unknown): value is BuyerProductItem {
  return hasExactKeys(value, [
    "entitlement_id",
    "resume_lesson_id",
    "seller",
    "product",
    "progress_percent"
  ]) && isUuid(value.entitlement_id) &&
    (value.resume_lesson_id === null || isUuid(value.resume_lesson_id)) &&
    isSeller(value.seller) && isProduct(value.product) &&
    Number.isInteger(value.progress_percent) &&
    typeof value.progress_percent === "number" &&
    value.progress_percent >= 0 && value.progress_percent <= 100;
}

function isSeller(value: unknown): value is BuyerProductItem["seller"] {
  return hasExactKeys(value, ["id", "slug", "name"]) && isUuid(value.id) &&
    isSlug(value.slug) && isTrimmedText(value.name);
}

function isProduct(value: unknown): value is BuyerProductItem["product"] {
  return hasExactKeys(value, [
    "id",
    "slug",
    "title",
    "description",
    "publication_id",
    "publication_version"
  ]) && isUuid(value.id) && isSlug(value.slug) && isTrimmedText(value.title) &&
    typeof value.description === "string" && value.description.length <= 2_000 &&
    isUuid(value.publication_id) && Number.isInteger(value.publication_version) &&
    typeof value.publication_version === "number" && value.publication_version > 0;
}

function hasExactKeys<T extends string>(
  value: unknown,
  keys: readonly T[]
): value is Record<T, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function isUuid(value: unknown): value is string {
  return typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);
}

function isSlug(value: unknown): value is string {
  return typeof value === "string" && value.length >= 3 && value.length <= 63 &&
    /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(value);
}

function isTrimmedText(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value === value.trim();
}
