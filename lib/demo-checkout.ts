export interface DemoCheckoutQuote {
  schema_version: 1;
  seller: { id: string; slug: string; name: string };
  product: { id: string; slug: string; title: string };
  offer: { id: string; unit_amount_minor: number; currency: "BRL" };
  already_owned: boolean;
  purchase_id: string | null;
  order_id: string | null;
  entitlement_id: string | null;
}

export interface DemoCheckoutResult {
  schema_version: 1;
  order_id: string;
  purchase_id: string | null;
  entitlement_id: string | null;
  subtotal_amount_minor: number;
  discount_amount_minor: number;
  total_amount_minor: 0;
  currency: "BRL";
  status: "processing" | "approved";
  provider_called: false;
  already_owned: boolean;
  applied: boolean;
}

export function parseDemoCheckoutEnvelope(value: unknown): DemoCheckoutQuote | DemoCheckoutResult | null {
  if (!isRecord(value) || !hasExactKeys(value, ["checkout"])) return null;
  return parseQuote(value.checkout) ?? parseResult(value.checkout);
}

function parseQuote(value: unknown): DemoCheckoutQuote | null {
  if (!isRecord(value) || !hasExactKeys(value, [
    "schema_version", "seller", "product", "offer", "already_owned",
    "purchase_id", "order_id", "entitlement_id"
  ]) || value.schema_version !== 1 || typeof value.already_owned !== "boolean") return null;
  if (!isId(value.purchase_id) || !isId(value.order_id) || !isId(value.entitlement_id)) return null;
  if (!isRecord(value.seller) || !hasExactKeys(value.seller, ["id", "slug", "name"]) ||
      !isUuid(value.seller.id) || !isSlug(value.seller.slug) || !isText(value.seller.name)) return null;
  if (!isRecord(value.product) || !hasExactKeys(value.product, ["id", "slug", "title"]) ||
      !isUuid(value.product.id) || !isSlug(value.product.slug) || !isText(value.product.title)) return null;
  if (!isRecord(value.offer) || !hasExactKeys(value.offer, ["id", "unit_amount_minor", "currency"]) ||
      !isUuid(value.offer.id) || !isMinor(value.offer.unit_amount_minor) || value.offer.currency !== "BRL") return null;
  return value as unknown as DemoCheckoutQuote;
}

function parseResult(value: unknown): DemoCheckoutResult | null {
  if (!isRecord(value) || !hasExactKeys(value, [
    "schema_version", "order_id", "purchase_id", "entitlement_id",
    "subtotal_amount_minor", "discount_amount_minor", "total_amount_minor", "currency",
    "status", "provider_called", "already_owned", "applied"
  ]) || value.schema_version !== 1 || !isUuid(value.order_id) ||
      !isId(value.purchase_id) || !isId(value.entitlement_id) ||
      !isMinor(value.subtotal_amount_minor) ||
      value.discount_amount_minor !== value.subtotal_amount_minor ||
      value.total_amount_minor !== 0 || value.currency !== "BRL" ||
      (value.status !== "processing" && value.status !== "approved") ||
      value.provider_called !== false || typeof value.already_owned !== "boolean" ||
      typeof value.applied !== "boolean") return null;
  return value as unknown as DemoCheckoutResult;
}

function hasExactKeys(value: Record<string, unknown>, expected: string[]) {
  const actual = Object.keys(value).sort();
  const keys = [...expected].sort();
  return actual.length === keys.length && actual.every((key, index) => key === keys[index]);
}
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function isUuid(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);
}
function isId(value: unknown): value is string | null { return value === null || isUuid(value); }
function isSlug(value: unknown): value is string {
  return typeof value === "string" && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(value);
}
function isText(value: unknown): value is string {
  return typeof value === "string" && value === value.trim() && value.length >= 2 && value.length <= 160;
}
function isMinor(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}
