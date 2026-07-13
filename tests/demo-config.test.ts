import { describe, expect, it } from "vitest";

import {
  DEMO_ADMIN_EMAIL,
  DEMO_ADMIN_SUBJECT,
  DEMO_BUYER_EMAIL,
  DEMO_BUYER_SUBJECT,
  demoModeEnabled,
  demoProfileForPersona,
  demoProfileForSubject
} from "../lib/demo-config";

const localDemo = { NODE_ENV: "development", APPSELL_DEMO_ENABLED: "true" };

describe("demo personas", () => {
  it("uses only the two fixed autonomia.site identities", () => {
    expect(demoProfileForPersona("admin", localDemo)).toMatchObject({
      subject: DEMO_ADMIN_SUBJECT,
      email: DEMO_ADMIN_EMAIL,
      home: "/dashboard"
    });
    expect(demoProfileForPersona("buyer", localDemo)).toMatchObject({
      subject: DEMO_BUYER_SUBJECT,
      email: DEMO_BUYER_EMAIL,
      home: "/library"
    });
    expect(DEMO_ADMIN_EMAIL.endsWith("@autonomia.site")).toBe(true);
    expect(DEMO_BUYER_EMAIL.endsWith("@autonomia.site")).toBe(true);
  });

  it("rejects arbitrary personas and subjects", () => {
    expect(demoProfileForPersona("superadmin", localDemo)).toBeNull();
    expect(demoProfileForSubject("attacker", localDemo)).toBeNull();
  });

  it("fails closed in production even when the demo flag is present", () => {
    const production = { NODE_ENV: "production", APPSELL_DEMO_ENABLED: "true" };
    expect(demoModeEnabled(production)).toBe(false);
    expect(demoProfileForPersona("admin", production)).toBeNull();
    expect(demoProfileForSubject(DEMO_BUYER_SUBJECT, production)).toBeNull();
  });
});
