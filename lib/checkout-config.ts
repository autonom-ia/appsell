// Interim product→Financial mapping until a per-product provisioning UI exists
// (Refs #96). `APPSELL_CHECKOUT_PRODUCT_MAP_JSON` is a strict JSON object of
// `"<sellerSlug>/<productSlug>": { "financial_product_key": "<key>" }` entries,
// hand-maintained until the seller/product editor can provision Financial
// products directly. Any malformed entry invalidates the whole map (fail
// closed) so a bad deploy cannot silently sell a misconfigured product.

// The only currency the Stripe checkout charges in slice 1, in Stripe's
// lowercase form. create-session prices with it and the webhook refuses any
// other, so the two cannot drift apart.
export const STRIPE_CHECKOUT_CURRENCY = "brl";

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

// Why a reverse lookup did not find exactly one product. The webhooks need the
// reason, not just the miss: a map that does not parse is an outage of this
// deployment (every paid event fails until it is fixed, so Stripe must keep
// redelivering), while a key that is absent or mapped twice is a fact about
// this one product that no redelivery changes.
export type CheckoutProductLookup =
  | { kind: "found"; sellerSlug: string; productSlug: string }
  | { kind: "not_mapped" }
  | { kind: "ambiguous" }
  | { kind: "map_invalid" };

export function lookupCheckoutProductByKey(financialProductKey: string): CheckoutProductLookup {
  const map = parseCheckoutProductMap(process.env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON);
  if (!map) return { kind: "map_invalid" };
  let found: { sellerSlug: string; productSlug: string } | null = null;
  for (const [key, mapping] of map) {
    if (mapping.financialProductKey !== financialProductKey) continue;
    if (found) return { kind: "ambiguous" };
    const [sellerSlug, productSlug] = key.split("/") as [string, string];
    found = { sellerSlug, productSlug };
  }
  return found ? { kind: "found", ...found } : { kind: "not_mapped" };
}

// Reverse lookup for the fulfillment webhooks: which seller/product does this
// financial_product_key belong to. Ambiguity (the same key mapped to two
// products) fails CLOSED — fulfilment must never guess which product a paid
// session was for.
export function findCheckoutProductByKey(
  financialProductKey: string
): { sellerSlug: string; productSlug: string } | null {
  const lookup = lookupCheckoutProductByKey(financialProductKey);
  return lookup.kind === "found" ? { sellerSlug: lookup.sellerSlug, productSlug: lookup.productSlug } : null;
}

// Readiness: a map that does not parse makes every mapped product unsellable
// and every paid Stripe event undeliverable. It must fail the deploy, not the
// buyers one by one.
export function checkoutProductMapIsValid(
  env: Partial<Record<string, string | undefined>> = process.env
): boolean {
  return parseCheckoutProductMap(env.APPSELL_CHECKOUT_PRODUCT_MAP_JSON) !== null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys<T extends string>(value: Record<string, unknown>, expected: readonly T[]): value is Record<T, unknown> {
  const actual = Object.keys(value).sort();
  const keys = [...expected].sort();
  return actual.length === keys.length && actual.every((key, index) => key === keys[index]);
}
