import { describe, expect, it } from "vitest";

import { isValidBuyerEmail, normalizeBuyerEmail } from "../lib/buyer-email";

// This module is the pre-charge mirror of the domain ingress contract
// (apps/api/src/appsell_api/webhook_adapters.py::_valid_buyer_email). Every case
// below states which clause of that contract it pins, because a divergence here
// means a buyer can pay and never receive access.
//
// Non-printable characters are written as \uNNNN escapes on purpose: a literal
// control byte in a source file makes it binary and unreviewable.
describe("normalizeBuyerEmail", () => {
  it("trims and lowercases", () => {
    expect(normalizeBuyerEmail("  Buyer@Example.TEST \t")).toBe("buyer@example.test");
  });

  it("is idempotent", () => {
    const once = normalizeBuyerEmail(" Buyer@Example.TEST ");
    expect(normalizeBuyerEmail(once)).toBe(once);
  });
});

describe("isValidBuyerEmail", () => {
  it("accepts a normalized address", () => {
    expect(isValidBuyerEmail("buyer@example.test")).toBe(true);
  });

  it("rejects anything not already normalized (ingress compares against strip().lower())", () => {
    expect(isValidBuyerEmail("Buyer@example.test")).toBe(false);
    expect(isValidBuyerEmail(" buyer@example.test")).toBe(false);
    expect(isValidBuyerEmail("buyer@example.test ")).toBe(false);
  });

  it("enforces the 6..242 OCTET bound, not a UTF-16 length", () => {
    // Exactly 6 octets is the lower bound: accepted.
    expect(isValidBuyerEmail("a@b.co")).toBe(true);
    // 5 octets: refused.
    expect(isValidBuyerEmail("a@b.c")).toBe(false);
    // Exactly 242 octets: accepted (229 local + 13 for "@example.test").
    const at242 = `${"a".repeat(229)}@example.test`;
    expect(new TextEncoder().encode(at242).length).toBe(242);
    expect(isValidBuyerEmail(at242)).toBe(true);
    // 243 octets: refused.
    expect(isValidBuyerEmail(`${"a".repeat(230)}@example.test`)).toBe(false);
  });

  it("rejects control characters, NUL and DEL (the ingress forbidden class)", () => {
    expect(isValidBuyerEmail("buyer\u0000@example.test")).toBe(false);
    expect(isValidBuyerEmail("buyer\u0001@example.test")).toBe(false);
    expect(isValidBuyerEmail("buyer\u001f@example.test")).toBe(false);
    expect(isValidBuyerEmail("buyer\u007f@example.test")).toBe(false);
    expect(isValidBuyerEmail("buyer\n@example.test")).toBe(false);
    expect(isValidBuyerEmail("buyer\t@example.test")).toBe(false);
  });

  it("rejects internal whitespace", () => {
    expect(isValidBuyerEmail("buyer name@example.test")).toBe(false);
    expect(isValidBuyerEmail("buyer@exa mple.test")).toBe(false);
  });

  it("rejects non-ASCII, where JS toLowerCase and Python .lower() can diverge", () => {
    expect(isValidBuyerEmail("buyer@ex\u00e4mple.test")).toBe(false);
    // U+00A0 NO-BREAK SPACE: not matched by the ingress' \s in Python's ASCII
    // sense but still not a valid address; refused here by the ASCII rule.
    expect(isValidBuyerEmail("buyer\u00a0@example.test")).toBe(false);
  });

  it("rejects addresses without a local part, an @, or a dotted domain", () => {
    expect(isValidBuyerEmail("@example.test")).toBe(false);
    expect(isValidBuyerEmail("buyer@")).toBe(false);
    expect(isValidBuyerEmail("buyerexample.test")).toBe(false);
    expect(isValidBuyerEmail("buyer@example")).toBe(false);
    expect(isValidBuyerEmail("buyer@@example.test")).toBe(false);
  });

  it("rejects the empty string", () => {
    expect(isValidBuyerEmail("")).toBe(false);
  });
});
