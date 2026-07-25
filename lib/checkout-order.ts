import { isIngressBuyerEmail, normalizeBuyerEmail } from "@/lib/buyer-email";

// Parses the registration-checkout order status response from Financial
// (GET .../registration-checkout/:orderId, reached server-side via
// lib/financial-order-client.server.ts).
//
// UNVERIFIED ASSUMPTION — flag before enabling in staging/production:
// this repo has no local access to the Financial service source or a running
// instance, so the exact response shape below (field names, paid-status
// values) is inferred from this codebase's own snake_case JSON conventions
// and from Stripe's PaymentIntent status vocabulary, not confirmed against
// the real Financial API. If the real contract differs, this is the single
// place to update. Refs #96.
const PAID_STATUSES: ReadonlySet<string> = new Set(["paid", "succeeded"]);

// Same safe-reference charset used for our own outbound webhook identifiers
// (lib/webhook-self-emit.server.ts) — printable, no separators that could
// break the HMAC canonical string or a URL path segment.
const SAFE_REFERENCE = /^[A-Za-z0-9._:-]{1,128}$/;

export interface RegistrationCheckoutOrderFacts {
  orderId: string;
  financialProductKey: string;
  buyerEmail: string;
  // Always normalized to Date#toISOString() (guarantees the trailing "Z"
  // the checkout-completion webhook payload requires) and always the order's
  // own paid_at, never wall-clock time, so retries of the same order produce
  // a byte-identical payload and are treated as an idempotent replay instead
  // of a conflicting event.
  occurredAt: string;
}

export function parsePaidRegistrationCheckoutOrder(
  expectedOrderId: string,
  value: unknown
): RegistrationCheckoutOrderFacts | null {
  if (!isRecord(value)) return null;
  const status = value.status;
  if (typeof status !== "string" || !PAID_STATUSES.has(status.toLowerCase())) return null;
  return parseOrderFacts(expectedOrderId, value);
}

export function isPendingRegistrationCheckoutOrder(expectedOrderId: string, value: unknown): boolean {
  if (!isRecord(value)) return false;
  const orderId = value.order_id;
  const status = value.status;
  return orderId === expectedOrderId && typeof status === "string" && !PAID_STATUSES.has(status.toLowerCase());
}

function parseOrderFacts(expectedOrderId: string, value: Record<string, unknown>): RegistrationCheckoutOrderFacts | null {
  const orderId = value.order_id;
  if (typeof orderId !== "string" || orderId !== expectedOrderId) return null;
  const financialProductKey = value.financial_product_key;
  if (typeof financialProductKey !== "string" || !SAFE_REFERENCE.test(financialProductKey)) return null;
  const rawBuyerEmail = value.buyer_email;
  if (typeof rawBuyerEmail !== "string") return null;
  // Financial may hand back a non-normalized address, so normalize FIRST and
  // validate the normalized form — that is the exact value the ingress will
  // receive and re-check.
  const buyerEmail = normalizeBuyerEmail(rawBuyerEmail);
  if (!isIngressBuyerEmail(buyerEmail)) return null;
  const paidAt = value.paid_at;
  const occurredAt = typeof paidAt === "string" ? normalizedIsoUtc(paidAt) : null;
  if (!occurredAt) return null;
  return { orderId, financialProductKey, buyerEmail, occurredAt };
}

function normalizedIsoUtc(value: string): string | null {
  if (value !== value.trim() || value.length === 0) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
