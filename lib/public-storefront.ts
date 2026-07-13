export interface PublicStorefrontProduct {
  id: string;
  slug: string;
  title: string;
  description: string;
  publication_id: string;
  publication_version: number;
}

export interface PublicStorefront {
  schema_version: 1;
  seller: { id: string; slug: string; name: string };
  products: PublicStorefrontProduct[];
}

export function parsePublicStorefront(value: unknown): PublicStorefront | null {
  if (!hasExactKeys(value, ["schema_version", "seller", "products"]) ||
      value.schema_version !== 1 || !isSeller(value.seller) || !Array.isArray(value.products)) {
    return null;
  }
  const products = new Set<string>();
  for (const product of value.products) {
    if (!isProduct(product) || products.has(product.id)) return null;
    products.add(product.id);
  }
  return value as PublicStorefront;
}

function isSeller(value: unknown) {
  return hasExactKeys(value, ["id", "slug", "name"]) && isUuid(value.id) &&
    isSlug(value.slug) && isTrimmedText(value.name, 2, 120);
}

function isProduct(value: unknown) {
  return hasExactKeys(value, [
    "id",
    "slug",
    "title",
    "description",
    "publication_id",
    "publication_version"
  ]) && isUuid(value.id) && isSlug(value.slug) && isTrimmedText(value.title, 2, 160) &&
    typeof value.description === "string" && value.description.length <= 2_000 &&
    isUuid(value.publication_id) && typeof value.publication_version === "number" &&
    Number.isInteger(value.publication_version) && value.publication_version > 0;
}

function hasExactKeys<T extends string>(
  value: unknown,
  expected: readonly T[]
): value is Record<T, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const keys = [...expected].sort();
  return actual.length === keys.length && actual.every((key, index) => key === keys[index]);
}

function isUuid(value: unknown): value is string {
  return typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);
}

function isSlug(value: unknown): value is string {
  return typeof value === "string" && value.length >= 3 && value.length <= 63 &&
    /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(value);
}

function isTrimmedText(value: unknown, minimum: number, maximum: number): value is string {
  return typeof value === "string" && value === value.trim() &&
    value.length >= minimum && value.length <= maximum;
}
