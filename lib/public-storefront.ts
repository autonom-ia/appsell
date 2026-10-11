const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export interface PublicSeller {
  slug: string;
  name: string;
}

export interface PublicProductCard {
  slug: string;
  title: string;
  description: string;
  course_count: number;
}

export interface PublicStorefrontProduct extends PublicProductCard {
  offer: { title: string; summary: string };
}

export interface PublicCourse {
  title: string;
  description: string;
  position: number;
}

export interface PublicProductDetail {
  slug: string;
  title: string;
  description: string;
  offer: { title: string; summary: string };
  courses: PublicCourse[];
}

export interface PublicStorefront {
  seller: PublicSeller;
  products: PublicStorefrontProduct[];
  next_cursor: string | null;
}

export interface PublicProductPage {
  seller: PublicSeller;
  product: PublicProductDetail;
  other_products: PublicProductCard[];
}

export function isPublicSlug(value: unknown): value is string {
  return typeof value === "string" && byteLength(value) >= 3 && byteLength(value) <= 63 && SLUG.test(value);
}

export function parsePublicStorefront(value: unknown): PublicStorefront | null {
  if (!exactRecord(value, ["next_cursor", "products", "seller"]) ||
      !isSeller(value.seller) || !Array.isArray(value.products) || value.products.length > 24 ||
      !(value.next_cursor === null || isPublicSlug(value.next_cursor))) return null;
  const products = value.products.filter(isStorefrontProduct);
  if (products.length !== value.products.length || new Set(products.map(({ slug }) => slug)).size !== products.length) {
    return null;
  }
  return { seller: value.seller, products, next_cursor: value.next_cursor };
}

export function parsePublicProductPage(value: unknown): PublicProductPage | null {
  if (!exactRecord(value, ["other_products", "product", "seller"])) return null;
  const { seller, product, other_products: rawOthers } = value;
  if (!isSeller(seller) || !isProductDetail(product) ||
      !Array.isArray(rawOthers) || rawOthers.length > 4) return null;
  const otherProducts = rawOthers.filter(isProductCard);
  if (otherProducts.length !== rawOthers.length ||
      otherProducts.some(({ slug }) => slug === product.slug) ||
      new Set(otherProducts.map(({ slug }) => slug)).size !== otherProducts.length) return null;
  return { seller, product, other_products: otherProducts };
}

function isSeller(value: unknown): value is PublicSeller {
  return exactRecord(value, ["name", "slug"]) && isPublicSlug(value.slug) &&
    boundedString(value.name, 2, 120, false);
}

function isStorefrontProduct(value: unknown): value is PublicStorefrontProduct {
  return exactRecord(value, ["course_count", "description", "offer", "slug", "title"]) &&
    isProductCore(value) && isOffer(value.offer);
}

function isProductCard(value: unknown): value is PublicProductCard {
  return exactRecord(value, ["course_count", "description", "slug", "title"]) && isProductCore(value);
}

function isProductCore(value: Record<string, unknown>): value is Record<string, unknown> & PublicProductCard {
  return isPublicSlug(value.slug) && boundedString(value.title, 2, 160, true) &&
    boundedString(value.description, 0, 2_000, false) && Number.isInteger(value.course_count) &&
    typeof value.course_count === "number" && value.course_count >= 0 && value.course_count <= 100;
}

function isProductDetail(value: unknown): value is PublicProductDetail {
  if (!exactRecord(value, ["courses", "description", "offer", "slug", "title"]) ||
      !isPublicSlug(value.slug) || !boundedString(value.title, 2, 160, true) ||
      !boundedString(value.description, 0, 2_000, false) || !isOffer(value.offer) ||
      !Array.isArray(value.courses) || value.courses.length > 100) {
    return false;
  }
  return value.courses.every((course) => exactRecord(course, ["description", "position", "title"]) &&
    boundedString(course.title, 2, 160, true) && boundedString(course.description, 0, 2_000, false) &&
    Number.isSafeInteger(course.position) && typeof course.position === "number" && course.position >= 0);
}

function isOffer(value: unknown): value is { title: string; summary: string } {
  return exactRecord(value, ["summary", "title"]) && boundedString(value.title, 2, 120, true) &&
    boundedString(value.summary, 0, 500, false);
}

function boundedString(value: unknown, minimum: number, maximum: number, requireTrim: boolean): value is string {
  if (typeof value !== "string" || (requireTrim && value !== value.trim()) || /[\p{Cc}\p{Cs}]/u.test(value)) return false;
  const length = byteLength(value);
  return length >= minimum && length <= maximum;
}

function byteLength(value: string) {
  return new TextEncoder().encode(value).length;
}

function exactRecord(value: unknown, keys: string[]): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}
