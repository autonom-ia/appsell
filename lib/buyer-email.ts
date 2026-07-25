// Single source of truth for the buyer e-mail contract.
//
// It mirrors, byte for byte, what the domain ingress accepts
// (apps/api/src/appsell_api/webhook_adapters.py::_valid_buyer_email):
//
//   value == value.strip().lower()
//   "@" at an index > 0
//   6 <= len(value.encode("utf-8")) <= 242
//   no character in [\x00-\x1f\x7f\s]
//
// Why it matters on the paid path: the Stripe webhook can only grant access if
// the ingress accepts the self-emitted payload. An e-mail that passes checkout
// but fails the ingress produces a charged buyer with no entitlement. So the
// SAME contract is enforced BEFORE the charge is created, not only after.
//
// Two divergent copies used to live in lib/checkout-order.ts (242 octets, no
// minimum) and lib/webhook-self-emit.server.ts (254 UTF-16 units); both are now
// thin callers of this module.

// Printable ASCII only, space excluded. This is stricter than the ingress on
// purpose: it forbids the control characters the ingress forbids, and it also
// guarantees that JavaScript's toLowerCase()/trim() agree with Python's
// .lower()/.strip() — outside ASCII the two case-fold differently, which would
// let a value pass here and fail the ingress equality check. Slice 1 has no
// internationalized-address requirement.
const PRINTABLE_ASCII_NO_SPACE = /^[!-~]+$/;
const SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MIN_OCTETS = 6;
const MAX_OCTETS = 242;

export function normalizeBuyerEmail(value: string): string {
  return value.trim().toLowerCase();
}

export function isValidBuyerEmail(value: string): boolean {
  // The ingress compares the received value against value.strip().lower(), so
  // anything not already normalized is rejected there — reject it here too
  // instead of silently emitting a payload that cannot be ingested.
  if (value !== normalizeBuyerEmail(value)) return false;
  if (!PRINTABLE_ASCII_NO_SPACE.test(value)) return false;
  // Octets, not UTF-16 units: the ingress bound is on the UTF-8 encoding.
  const octets = new TextEncoder().encode(value).length;
  if (octets < MIN_OCTETS || octets > MAX_OCTETS) return false;
  return SHAPE.test(value);
}
