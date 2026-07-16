import { describe, expect, it } from "vitest";

import {
  PUBLIC_DEMO_RUNTIME_MARKER,
  publicDemoRuntimeEnabled
} from "../lib/public-demo-runtime";
import { demoModeEnabled, demoProfileForPersona } from "../lib/demo-config";
import { canAccessDemoPreview } from "../lib/demo-view";
import { oauthRuntimeMode } from "../lib/oauth-runtime-mode";

const valid = {
  NODE_ENV: "production",
  APP_ENV: "ci",
  APPSELL_PUBLIC_DEMO_RUNTIME: PUBLIC_DEMO_RUNTIME_MARKER,
  APPSELL_PUBLIC_ORIGIN: "https://sell.autonomia.site",
  APPSELL_OAUTH_MODE: "local",
  APPSELL_DEMO_ENABLED: "true",
  APPSELL_IDENTITY_DEMO_ENABLED: "false",
  APPSELL_DEMO_SUPERADMIN_ENABLED: "true",
  APPSELL_REDIS_PREFIX: "appsell:runtime-preview:issue77",
  APPSELL_IDENTITY_ISSUER: "https://identity.runtime-preview.local",
  APPSELL_INTERNAL_ISSUER: "appsell-bff-runtime-preview-77",
  APPSELL_INTERNAL_AUDIENCE: "appsell-api-runtime-preview-77",
  APPSELL_DOMAIN_API_URL: "http://domain-api:15800",
  APPSELL_LOCAL_IDENTITY_EMAIL: "creator.runtime-preview@autonomia.site"
};

describe("public demo runtime", () => {
  it("requires the complete immutable production-preview contract", () => {
    expect(publicDemoRuntimeEnabled(valid)).toBe(true);
    expect(demoModeEnabled(valid)).toBe(true);
    expect(demoProfileForPersona("admin", valid)?.email).toBe("admin.demo@autonomia.site");
    expect(oauthRuntimeMode(valid)).toBe("local");
    expect(canAccessDemoPreview("demo-user-v1", "production", valid)).toBe(true);
  });

  it.each(Object.keys(valid))("fails closed when %s drifts", (key) => {
    expect(publicDemoRuntimeEnabled({ ...valid, [key]: "drifted" })).toBe(false);
    const missing = { ...valid } as Record<string, string | undefined>;
    delete missing[key];
    expect(publicDemoRuntimeEnabled(missing)).toBe(false);
  });
});
