import { describe, expect, it } from "vitest";

import { isChargeableBuyerEmail, isIngressBuyerEmail, normalizeBuyerEmail } from "../lib/buyer-email";

// Two contracts, opposite failure modes:
//
// isIngressBuyerEmail runs POST-charge. Stricter than the ingress here = paid
// order that can never be fulfilled. It must mirror
// apps/api/src/appsell_api/webhook_adapters.py::_valid_buyer_email exactly.
//
// isChargeableBuyerEmail runs PRE-charge. Stricter is free: no money moved.
//
// Non-printable characters are written as \uNNNN escapes on purpose: a literal
// control byte in a source file makes it binary and unreviewable.

describe("normalizeBuyerEmail", () => {
  it("lowercases and strips the whitespace Python strips", () => {
    expect(normalizeBuyerEmail("  Buyer@Example.TEST \t")).toBe("buyer@example.test");
    expect(normalizeBuyerEmail("\u00a0buyer@example.test\u3000")).toBe("buyer@example.test");
  });

  it("does NOT strip U+FEFF, because Python's str.strip() does not", () => {
    // String#trim would remove it. Removing it here would make us reject an
    // address the ingress accepts — post-charge, a paid buyer with no access.
    expect(normalizeBuyerEmail("\ufeffbuyer@example.test")).toBe("\ufeffbuyer@example.test");
  });

  it("is idempotent", () => {
    const once = normalizeBuyerEmail(" Buyer@Example.TEST ");
    expect(normalizeBuyerEmail(once)).toBe(once);
  });
});

describe("isIngressBuyerEmail (post-charge: must not be stricter than the ingress)", () => {
  it("accepts a normalized address", () => {
    expect(isIngressBuyerEmail("buyer@example.test")).toBe(true);
  });

  it("accepts non-ASCII, which the ingress accepts", () => {
    // REGRESSION GUARD. Applying the pre-charge (ASCII-only) rule here made
    // parsePaidRegistrationCheckoutOrder return null for an address the
    // registration UI and the ingress both accept, so /api/checkout/complete
    // answered 503 forever on an order the buyer had ALREADY paid.
    expect(isIngressBuyerEmail("jos\u00e9@example.com")).toBe(true);
    expect(isIngressBuyerEmail("\u00dfs@example.test")).toBe(true);
    expect(isIngressBuyerEmail("buyer@ex\u00e4mple.test")).toBe(true);
  });

  it("accepts a dotless domain, which the ingress accepts", () => {
    expect(isIngressBuyerEmail("buyer@example")).toBe(true);
  });

  it("rejects an address that is not already lowercase", () => {
    expect(isIngressBuyerEmail("Buyer@example.test")).toBe(false);
  });

  it("rejects control characters, NUL and DEL", () => {
    expect(isIngressBuyerEmail("buyer\u0000@example.test")).toBe(false);
    expect(isIngressBuyerEmail("buyer\u0001@example.test")).toBe(false);
    expect(isIngressBuyerEmail("buyer\u001f@example.test")).toBe(false);
    expect(isIngressBuyerEmail("buyer\u007f@example.test")).toBe(false);
    expect(isIngressBuyerEmail("buyer\n@example.test")).toBe(false);
    expect(isIngressBuyerEmail("buyer\t@example.test")).toBe(false);
  });

  it("rejects whitespace anywhere, including the Unicode kinds Python matches", () => {
    expect(isIngressBuyerEmail("buyer @example.test")).toBe(false);
    expect(isIngressBuyerEmail("buyer\u00a0@example.test")).toBe(false);
    expect(isIngressBuyerEmail("buyer\u2000@example.test")).toBe(false);
    expect(isIngressBuyerEmail("buyer\u0085@example.test")).toBe(false);
    expect(isIngressBuyerEmail("buyer@example.test\u3000")).toBe(false);
  });

  it("enforces the 6..242 OCTET bound, not a UTF-16 length", () => {
    expect(isIngressBuyerEmail("a@b.co")).toBe(true);
    expect(isIngressBuyerEmail("a@b.c")).toBe(false);
    const at242 = `${"a".repeat(229)}@example.test`;
    expect(new TextEncoder().encode(at242).length).toBe(242);
    expect(isIngressBuyerEmail(at242)).toBe(true);
    expect(isIngressBuyerEmail(`${"a".repeat(230)}@example.test`)).toBe(false);
    // Multi-byte: 121 x "\u00e9" is 242 octets but only 121 UTF-16 units.
    const multibyte = `${"\u00e9".repeat(115)}@example.test`;
    expect(new TextEncoder().encode(multibyte).length).toBe(243);
    expect(isIngressBuyerEmail(multibyte)).toBe(false);
  });

  it("rejects a lone surrogate, which has no UTF-8 encoding", () => {
    // TextEncoder substitutes U+FFFD, so the octet check alone would accept a
    // value that Python's value.encode("utf-8") raises UnicodeEncodeError on.
    expect(isIngressBuyerEmail("a@\ud800.co")).toBe(false);
    expect(isIngressBuyerEmail("a@\udc00.co")).toBe(false);
    // A well-formed pair is a real code point and must still be accepted.
    expect(isIngressBuyerEmail("a@\u{1f600}.co")).toBe(true);
  });

  it("requires an @ that is not the first character", () => {
    expect(isIngressBuyerEmail("@example.test")).toBe(false);
    expect(isIngressBuyerEmail("buyerexample.test")).toBe(false);
    expect(isIngressBuyerEmail("")).toBe(false);
  });
});

describe("isChargeableBuyerEmail (pre-charge: stricter on purpose)", () => {
  it("accepts a plain ASCII address", () => {
    expect(isChargeableBuyerEmail("buyer@example.test")).toBe(true);
    expect(isChargeableBuyerEmail("buyer+tag@sub.example.test")).toBe(true);
  });

  it("rejects non-ASCII so JS toLowerCase cannot disagree with Python .lower()", () => {
    // Refusing at checkout costs a conversion; accepting and failing the ingress
    // afterwards costs a paid order with no access. Tracked in #146.
    expect(isChargeableBuyerEmail("jos\u00e9@example.com")).toBe(false);
    expect(isChargeableBuyerEmail("\ufeffbuyer@example.test")).toBe(false);
  });

  it("requires a dotted domain, unlike the ingress", () => {
    expect(isChargeableBuyerEmail("buyer@example")).toBe(false);
  });

  it("inherits every ingress rejection", () => {
    for (const value of [
      "Buyer@example.test",
      "buyer\u0000@example.test",
      "buyer @example.test",
      "a@b.c",
      "@example.test",
      ""
    ]) {
      expect(isIngressBuyerEmail(value)).toBe(false);
      expect(isChargeableBuyerEmail(value)).toBe(false);
    }
  });

  it("is never more permissive than the ingress predicate", () => {
    for (const value of [
      "buyer@example.test",
      "jos\u00e9@example.com",
      "buyer@example",
      "a@b.co",
      "Buyer@example.test",
      "buyer\u00a0@example.test"
    ]) {
      if (isChargeableBuyerEmail(value)) expect(isIngressBuyerEmail(value)).toBe(true);
    }
  });
});
