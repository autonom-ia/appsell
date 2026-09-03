export interface StorefrontPrice {
  unit_amount_minor: number;
  currency: string;
  billing_period: "one_time" | "monthly" | "yearly";
}

/** Uma imagem já presignada pela API: endereço temporário, nunca chave. */
export interface StorefrontImage {
  url: string;
  media_type: string;
  width: number;
  height: number;
  expires_in_seconds?: number;
}

/** O bloco "o que você vai receber": só títulos e contagens da versão publicada. */
export interface StorefrontCurriculum {
  lesson_count: number;
  material_count: number;
  lessons: string[];
}

export interface PublicStorefrontProduct {
  id: string;
  slug: string;
  title: string;
  description: string;
  publication_id: string;
  publication_version: number;
  // Optional only while an old API instance is being replaced. New catalogs
  // always expose the access mode explicitly.
  access_mode?: "free" | "paid";
  price: StorefrontPrice | null;
  // A2 (#352): os três só existem quando o vendedor os definiu. A ausência é
  // a forma antiga e continua válida.
  headline?: string;
  cover?: StorefrontImage;
  curriculum?: StorefrontCurriculum;
}

export interface PublicStorefrontSeller {
  id: string;
  slug: string;
  name: string;
  // A2 (#352): marca da loja, cada campo só quando existe.
  tagline?: string;
  about?: string;
  accent_color?: string;
  logo?: StorefrontImage;
  banner?: StorefrontImage;
}

// Mirrors the offer table CHECK and API ceiling (999,999,999.99 in centavos).
const MAX_UNIT_AMOUNT_MINOR = 99_999_999_999;
const BILLING_PERIODS: ReadonlySet<string> = new Set(["one_time", "monthly", "yearly"]);
const MAX_CURRICULUM_TITLES = 40;
const HEX_COLOR = /^#[0-9a-f]{6}$/;

export interface PublicStorefront {
  schema_version: 1;
  seller: PublicStorefrontSeller;
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

function isSeller(value: unknown): value is PublicStorefrontSeller {
  if (!hasKeysWithin(value, ["id", "slug", "name"], ["tagline", "about", "accent_color", "logo", "banner"])) {
    return false;
  }
  return isUuid(value.id) && isSlug(value.slug) && isTrimmedText(value.name, 2, 120) &&
    (value.tagline === undefined || isTrimmedText(value.tagline, 2, 160)) &&
    (value.about === undefined || isTrimmedText(value.about, 2, 600)) &&
    (value.accent_color === undefined ||
      (typeof value.accent_color === "string" && HEX_COLOR.test(value.accent_color))) &&
    (value.logo === undefined || isImage(value.logo)) &&
    (value.banner === undefined || isImage(value.banner));
}

function isProduct(value: unknown): value is PublicStorefrontProduct {
  const required = [
    "id",
    "slug",
    "title",
    "description",
    "publication_id",
    "publication_version",
    "price"
  ] as const;
  if (!hasKeysWithin(value, required, ["access_mode", "headline", "cover", "curriculum"])) return false;
  return isUuid(value.id) && isSlug(value.slug) && isTrimmedText(value.title, 2, 160) &&
    typeof value.description === "string" && value.description.length <= 2_000 &&
    isUuid(value.publication_id) && typeof value.publication_version === "number" &&
    Number.isInteger(value.publication_version) && value.publication_version > 0 &&
    (value.access_mode === undefined || value.access_mode === "free" || value.access_mode === "paid") &&
    (value.price === null || isPrice(value.price)) &&
    (value.headline === undefined || isTrimmedText(value.headline, 2, 200)) &&
    (value.cover === undefined || isImage(value.cover)) &&
    (value.curriculum === undefined || isCurriculum(value.curriculum));
}

function isPrice(value: unknown): value is StorefrontPrice {
  return hasExactKeys(value, ["unit_amount_minor", "currency", "billing_period"]) &&
    typeof value.unit_amount_minor === "number" &&
    Number.isInteger(value.unit_amount_minor) &&
    value.unit_amount_minor >= 0 && value.unit_amount_minor <= MAX_UNIT_AMOUNT_MINOR &&
    typeof value.currency === "string" && /^[A-Z]{3}$/.test(value.currency) &&
    typeof value.billing_period === "string" && BILLING_PERIODS.has(value.billing_period);
}

// A imagem chega presignada. Chave ou versão de objeto aqui é defeito da API
// e derruba o parser: melhor uma vitrine sem imagem do que uma que vaza.
function isImage(value: unknown): value is StorefrontImage {
  if (!hasKeysWithin(value, ["url", "media_type", "width", "height"], ["expires_in_seconds"])) {
    return false;
  }
  return typeof value.url === "string" && value.url.startsWith("https://") &&
    typeof value.media_type === "string" && value.media_type.startsWith("image/") &&
    isDimension(value.width) && isDimension(value.height) &&
    (value.expires_in_seconds === undefined ||
      (typeof value.expires_in_seconds === "number" && Number.isInteger(value.expires_in_seconds)));
}

function isCurriculum(value: unknown): value is StorefrontCurriculum {
  if (!hasExactKeys(value, ["lesson_count", "material_count", "lessons"])) return false;
  return typeof value.lesson_count === "number" && Number.isInteger(value.lesson_count) &&
    value.lesson_count >= 1 &&
    typeof value.material_count === "number" && Number.isInteger(value.material_count) &&
    value.material_count >= 0 &&
    Array.isArray(value.lessons) && value.lessons.length <= MAX_CURRICULUM_TITLES &&
    value.lessons.length <= value.lesson_count &&
    value.lessons.every((title) => typeof title === "string" && title.length >= 1 && title.length <= 200);
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

/** As chaves obrigatórias têm de estar; as opcionais podem; nenhuma outra. */
function hasKeysWithin<T extends string, O extends string>(
  value: unknown,
  required: readonly T[],
  optional: readonly O[]
): value is Record<T, unknown> & Partial<Record<O, unknown>> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value);
  const allowed = new Set<string>([...required, ...optional]);
  return required.every((key) => Object.hasOwn(value, key)) &&
    actual.every((key) => allowed.has(key));
}

function isDimension(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 4000;
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
