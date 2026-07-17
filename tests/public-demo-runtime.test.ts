import { describe, expect, it } from "vitest";

import {
  PUBLIC_DEMO_RUNTIME_MARKER,
  PUBLIC_DEMO_STAGING_RUNTIME_MARKER,
  publicDemoRuntimeEnabled
} from "../lib/public-demo-runtime";
import { demoModeEnabled, demoProfileForPersona } from "../lib/demo-config";
import { canAccessDemoPreview } from "../lib/demo-view";
import { oauthRuntimeMode } from "../lib/oauth-runtime-mode";

const shared = {
  NODE_ENV: "production",
  APP_ENV: "ci",
  APPSELL_OAUTH_MODE: "local",
  APPSELL_DEMO_ENABLED: "true",
  APPSELL_IDENTITY_DEMO_ENABLED: "false",
  APPSELL_DEMO_SUPERADMIN_ENABLED: "true",
  APPSELL_REDIS_PREFIX: "appsell:native:issue90",
  APPSELL_IDENTITY_ISSUER: "https://identity.native.local",
  APPSELL_INTERNAL_ISSUER: "appsell-bff-native-90",
  APPSELL_INTERNAL_AUDIENCE: "appsell-api-native-90",
  APPSELL_DOMAIN_API_URL: "http://sell-autonomia_api:8000",
  APPSELL_LOCAL_IDENTITY_EMAIL: "runtime.native@autonomia.site"
};

const staging = {
  ...shared,
  APPSELL_PUBLIC_DEMO_RUNTIME: PUBLIC_DEMO_STAGING_RUNTIME_MARKER,
  APPSELL_PUBLIC_ORIGIN: "https://sell-issue90.85-31-60-100.sslip.io"
};

describe("public demo runtime", () => {
  it("keeps the compatibility marker constrained to staging", () => {
    expect(PUBLIC_DEMO_RUNTIME_MARKER).toBe(PUBLIC_DEMO_STAGING_RUNTIME_MARKER);
  });

  it("enables only the complete immutable staging contract", () => {
    expect(publicDemoRuntimeEnabled(staging)).toBe(true);
    expect(demoModeEnabled(staging)).toBe(true);
    expect(demoProfileForPersona("admin", staging)?.email).toBe("admin.demo@autonomia.site");
    expect(oauthRuntimeMode(staging)).toBe("local");
    expect(canAccessDemoPreview("demo-user-v1", "production", staging)).toBe(true);
  });

  it("fails closed when a required staging value drifts or is missing", () => {
    for (const key of Object.keys(staging)) {
      expect(publicDemoRuntimeEnabled({ ...staging, [key]: "drifted" })).toBe(false);
      const missing = { ...staging } as Record<string, string | undefined>;
      delete missing[key];
      expect(publicDemoRuntimeEnabled(missing)).toBe(false);
    }
  });

  it("rejects the public production origin for the local demo runtime", () => {
    expect(publicDemoRuntimeEnabled({
      ...staging,
      APPSELL_PUBLIC_ORIGIN: "https://sell.autonomia.site"
    })).toBe(false);
  });
});
