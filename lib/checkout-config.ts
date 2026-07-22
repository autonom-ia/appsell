// Interim product→Financial mapping until a per-product provisioning UI exists
// (Refs #96). `APPSELL_CHECKOUT_PRODUCT_MAP_JSON` is a strict JSON object of
// `"<sellerSlug>/<productSlug>": { "financial_product_key": "<key>" }` entries,
// hand-maintained until the seller/product editor can provision Financial
// products directly. Any malformed entry invalidates the whole map (fail
// closed) so a bad deploy cannot silently sell a misconfigured product.

export interface CheckoutProductMapping {
  financialProductKey: string;
}

const MAP_KEY = /^[a-z0-9]+(?:-[a-z0-9]+)*\/[a-z0-9]+(?:-[a-z0-9]+)*$/;
const FINANCIAL_PRODUCT_KEY = /^[A-Za-z0-9._-]{1,128}$/;

export function parseCheckoutProductMap(raw: string | undefined): Map<string, CheckoutProductMapping> | null {
  if (raw === undefined || raw === "") return new Map();
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(parsed)) return null;
  const map = new Map<string, CheckoutProductMapping>();
  for (const [key, value] of Object.entries(parsed)) {
    if (!MAP_KEY.test(key)) return null;
    if (!isRecord(value) || !hasExactKeys(value, ["financial_product_key"])) return null;
    const financialProductKey = value.financial_product_key;
    if (typeof financialProductKey !== "string" || !FINANCIAL_PRODUCT_KEY.test(financialProductKey)) return null;
    map.set(key, { financialProductKey });
  }
  return map;
}

export function resolveCheckoutProduct(sellerSlug: string, productSlug: string): CheckoutProductMapping | null {
  const map = parseCheckoutProductMap(process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON);
  if (!map) return null;
  return map.get(`${sellerSlug}/${productSlug}`) ?? null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys<T extends string>(value: Record<string, unknown>, expected: readonly T[]): value is Record<T, unknown> {
  const actual = Object.keys(value).sort();
  const keys = [...expected].sort();
  return actual.length === keys.length && actual.every((key, index) => key === keys[index]);
}
