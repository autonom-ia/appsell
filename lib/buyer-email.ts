// Buyer e-mail contracts. There are TWO, and conflating them causes the exact
// bug this module exists to prevent.
//
// `isIngressBuyerEmail` — POST-CHARGE boundary. An exact mirror of
// apps/api/src/appsell_api/webhook_adapters.py::_valid_buyer_email. It must
// never be stricter than the ingress: by the time it runs the buyer has already
// paid, so rejecting an address the ingress would have accepted produces a paid
// order that can never be fulfilled.
//
// `isChargeableBuyerEmail` — PRE-CHARGE gate. Ingress parity PLUS extra
// restrictions (printable ASCII, dotted domain). Being stricter here is free:
// no money has moved, the buyer just fixes the field. The ASCII rule buys
// certainty that JavaScript's toLowerCase() agrees with Python's .lower(), so a
// value that passes checkout cannot fail the ingress equality check later.
//
// The ingress contract, verbatim from webhook_adapters.py:
//   value == value.strip().lower()
//   "@" at an index > 0
//   6 <= len(value.encode("utf-8")) <= 242
//   no character matching r"[\x00-\x1f\x7f\s]"

// Python's `\s` for str patterns is Unicode-aware: exactly the characters where
// str.isspace() is true. Enumerated explicitly instead of using JavaScript's
// `\s`, which ALSO matches U+FEFF while Python's does not. That difference is
// not cosmetic: String#trim strips U+FEFF, so normalizing with trim() and then
// demanding "already normalized" would reject "<U+FEFF>a@b.co" — an address the
// ingress accepts. Post-charge, that is a paid buyer with no access.
const PY_WHITESPACE_CLASS = "\\t\\n\\v\\f\\r \\u001c-\\u001f\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const PY_WHITESPACE_ANYWHERE = new RegExp(`[${PY_WHITESPACE_CLASS}]`);
const PY_WHITESPACE_LEADING = new RegExp(`^[${PY_WHITESPACE_CLASS}]+`);
const PY_WHITESPACE_TRAILING = new RegExp(`[${PY_WHITESPACE_CLASS}]+$`);

// The \x00-\x1f\x7f half of the ingress' forbidden class.
const CONTROL_OR_DEL = /[\u0000-\u001f\u007f]/;

// Pre-charge only: printable ASCII, space excluded.
const PRINTABLE_ASCII_NO_SPACE = /^[!-~]+$/;
// Pre-charge only: the ingress is happy with "buyer@example", a real checkout
// field is not.
const DOTTED_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const MIN_OCTETS = 6;
const MAX_OCTETS = 242;

// Equivalent to Python's value.strip().lower() for the characters the ingress
// cares about. NOT String#trim — see PY_WHITESPACE_CLASS above.
export function normalizeBuyerEmail(value: string): string {
  return value.replace(PY_WHITESPACE_LEADING, "").replace(PY_WHITESPACE_TRAILING, "").toLowerCase();
}

export function isIngressBuyerEmail(value: string): boolean {
  if (CONTROL_OR_DEL.test(value)) return false;
  // The ingress searches its forbidden class over the WHOLE value, so any
  // whitespace anywhere is rejected — which also makes strip() a no-op below.
  if (PY_WHITESPACE_ANYWHERE.test(value)) return false;
  // With no whitespace left, `value == value.strip().lower()` reduces to "is
  // already lowercase".
  if (value !== value.toLowerCase()) return false;
  if (value.indexOf("@") <= 0) return false;
  // Octets, not UTF-16 units: the ingress bound is on the UTF-8 encoding.
  const octets = new TextEncoder().encode(value).length;
  return octets >= MIN_OCTETS && octets <= MAX_OCTETS;
}

export function isChargeableBuyerEmail(value: string): boolean {
  if (!isIngressBuyerEmail(value)) return false;
  if (!PRINTABLE_ASCII_NO_SPACE.test(value)) return false;
  return DOTTED_SHAPE.test(value);
}
