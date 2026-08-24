import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import { learningLicenseLabel, maskLicenseEmail } from "../lib/learning-license.server";

describe("protected PDF license label", () => {
  it("masks the buyer email and keeps a short deterministic trace", () => {
    const viewer = {
      subject: "identity|buyer-123",
      identity: { email: "comercial@hub2you.ai", emailVerified: true as const }
    };
    const first = learningLicenseLabel(viewer, "entitlement-123");
    const repeated = learningLicenseLabel(viewer, "entitlement-123");
    const anotherAccess = learningLicenseLabel(viewer, "entitlement-456");

    expect(first).toMatch(/^Licenciado para co\*\*\*@hub2you\.ai • ID [0-9a-f]{6}$/);
    expect(first).toBe(repeated);
    expect(first).not.toBe(anotherAccess);
    expect(first).not.toContain("comercial@");
  });

  it("fails closed when the email is malformed", () => {
    expect(maskLicenseEmail("invalid-email")).toBe("acesso protegido");
    expect(maskLicenseEmail("a@example.test")).toBe("a***@example.test");
  });
});
