import { describe, expect, it } from "vitest";

import type { DemoProfile } from "../lib/demo-config";
import { oauthCallbackHome } from "../lib/oauth-callback-home";

function profile(persona: DemoProfile["persona"], home: DemoProfile["home"]): DemoProfile {
  return {
    persona,
    home,
    subject: `${persona}-subject`,
    email: `${persona}@example.test`,
    label: persona
  };
}

describe("OAuth callback home", () => {
  it("keeps every real identity, including a platform superadmin, on the real dashboard", () => {
    expect(oauthCallbackHome(null)).toBe("/dashboard");
  });

  it("preserves the isolated demo persona destinations", () => {
    expect(oauthCallbackHome(profile("admin", "/dashboard"))).toBe("/dashboard");
    expect(oauthCallbackHome(profile("buyer", "/library"))).toBe("/library");
    expect(oauthCallbackHome(profile("superadmin", "/platform/demo-inspection")))
      .toBe("/platform/demo-inspection");
  });
});
