import { describe, expect, it } from "vitest";

import {
  DEMO_ADMIN_EMAIL,
  DEMO_ADMIN_SUBJECT,
  DEMO_BUYER_EMAIL,
  DEMO_BUYER_SUBJECT,
  IDENTITY_DEMO_ADMIN_SUBJECT,
  IDENTITY_DEMO_BUYER_SUBJECT,
  demoModeEnabled,
  identityDemoModeEnabled,
  demoProfileForPersona,
  demoProfileForSubject
} from "../lib/demo-config";

const localDemo = { NODE_ENV: "development", APPSELL_DEMO_ENABLED: "true" };
const localIdentityDemo = { NODE_ENV: "development", APPSELL_IDENTITY_DEMO_ENABLED: "true" };

describe("demo personas", () => {
  it("uses the two public demo identities", () => {
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
    expect(demoProfileForSubject(IDENTITY_DEMO_ADMIN_SUBJECT, localIdentityDemo)).toMatchObject({
      persona: "admin",
      email: DEMO_ADMIN_EMAIL
    });
    expect(demoProfileForSubject(IDENTITY_DEMO_BUYER_SUBJECT, localIdentityDemo)).toMatchObject({
      persona: "buyer",
      email: DEMO_BUYER_EMAIL
    });
  });

  it("rejects arbitrary personas and subjects", () => {
    expect(demoProfileForPersona("superadmin", localDemo)).toBeNull();
    expect(demoProfileForPersona("superadmin", {
      ...localDemo,
      APPSELL_DEMO_SUPERADMIN_ENABLED: "true"
    })).toMatchObject({
      subject: "inspection-superadmin",
      email: "comercial@autonomia.site",
      home: "/platform/demo-inspection"
    });
    expect(demoProfileForSubject("attacker", localDemo)).toBeNull();
  });

  it("fails closed in production even when the demo flag is present", () => {
    const production = {
      NODE_ENV: "production",
      APPSELL_DEMO_ENABLED: "true",
      APPSELL_DEMO_SUPERADMIN_ENABLED: "true"
    };
    expect(demoModeEnabled(production)).toBe(false);
    expect(identityDemoModeEnabled({ ...production, APPSELL_IDENTITY_DEMO_ENABLED: "true" })).toBe(false);
    expect(demoProfileForPersona("admin", production)).toBeNull();
    expect(demoProfileForSubject(DEMO_BUYER_SUBJECT, production)).toBeNull();
    expect(demoProfileForPersona("superadmin", production)).toBeNull();
    expect(demoProfileForSubject(IDENTITY_DEMO_ADMIN_SUBJECT, {
      ...production,
      APPSELL_IDENTITY_DEMO_ENABLED: "true"
    })).toBeNull();
  });
});
