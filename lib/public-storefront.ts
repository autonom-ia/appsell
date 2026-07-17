export interface StorefrontPrice {
  unit_amount_minor: number;
  currency: string;
  billing_period: "one_time" | "monthly" | "yearly";
}

export interface PublicStorefrontProduct {
  id: string;
  slug: string;
  title: string;
  description: string;
  publication_id: string;
  publication_version: number;
  // Live offer price exposed by app_private.read_public_storefront (migration 0018).
  // Billing is not connected yet, so unpriced/legacy offers carry unit_amount_minor 0,
  // which means "price pending", not "free". The storefront intentionally does not
  // render a price until billing is connected, so no product advertises R$ 0,00.
  price: StorefrontPrice;
}

// Mirrors the offer table CHECK and API ceiling (999,999,999.99 in centavos).
const MAX_UNIT_AMOUNT_MINOR = 99_999_999_999;
const BILLING_PERIODS: ReadonlySet<string> = new Set(["one_time", "monthly", "yearly"]);

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
    "publication_version",
    "price"
  ]) && isUuid(value.id) && isSlug(value.slug) && isTrimmedText(value.title, 2, 160) &&
    typeof value.description === "string" && value.description.length <= 2_000 &&
    isUuid(value.publication_id) && typeof value.publication_version === "number" &&
    Number.isInteger(value.publication_version) && value.publication_version > 0 &&
    isPrice(value.price);
}

function isPrice(value: unknown): value is StorefrontPrice {
  return hasExactKeys(value, ["unit_amount_minor", "currency", "billing_period"]) &&
    typeof value.unit_amount_minor === "number" &&
    Number.isInteger(value.unit_amount_minor) &&
    value.unit_amount_minor >= 0 && value.unit_amount_minor <= MAX_UNIT_AMOUNT_MINOR &&
    typeof value.currency === "string" && /^[A-Z]{3}$/.test(value.currency) &&
    typeof value.billing_period === "string" && BILLING_PERIODS.has(value.billing_period);
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
