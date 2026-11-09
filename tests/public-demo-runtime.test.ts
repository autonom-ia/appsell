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
  APPSELL_PUBLIC_ORIGIN: "https://demo-staging.example.test",
  APPSELL_PUBLIC_DEMO_ORIGIN: "https://demo-staging.example.test"
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
    expect(publicDemoRuntimeEnabled({
      ...staging,
      APPSELL_PUBLIC_ORIGIN: "https://sell.autonomia.site",
      APPSELL_PUBLIC_DEMO_ORIGIN: "https://sell.autonomia.site"
    })).toBe(false);
  });

  it("rejects spelling variants of the production origin", () => {
    const variants = [
      "https://SELL.autonomia.site",
      "https://Sell.Autonomia.Site",
      "https://sell.autonomia.site/",
      "https://sell.autonomia.site:443",
      "https://sell.autonomia.site.",
      "https://sell.autonomia.site./"
    ];
    for (const origin of variants) {
      expect(publicDemoRuntimeEnabled({
        ...staging,
        APPSELL_PUBLIC_ORIGIN: origin,
        APPSELL_PUBLIC_DEMO_ORIGIN: origin
      })).toBe(false);
    }
  });

  it("rejects the production domain and any of its subdomains", () => {
    for (const origin of [
      "https://autonomia.site",
      "https://demo.autonomia.site",
      "https://a.b.autonomia.site",
      "https://AUTONOMIA.SITE"
    ]) {
      expect(publicDemoRuntimeEnabled({
        ...staging,
        APPSELL_PUBLIC_ORIGIN: origin,
        APPSELL_PUBLIC_DEMO_ORIGIN: origin
      })).toBe(false);
    }
  });

  it("requires the declared demo origin to be a canonical origin", () => {
    for (const origin of [
      "https://demo-staging.example.test/",
      "https://demo-staging.example.test:443",
      "https://DEMO-staging.example.test",
      "https://demo-staging.example.test.",
      "https://user@demo-staging.example.test",
      "https://user:pass@demo-staging.example.test",
      "https://demo-staging.example.test/path",
      "https://demo-staging.example.test?x=1",
      "https://demo-staging.example.test#x",
      "not a url"
    ]) {
      expect(publicDemoRuntimeEnabled({
        ...staging,
        APPSELL_PUBLIC_ORIGIN: origin,
        APPSELL_PUBLIC_DEMO_ORIGIN: origin
      })).toBe(false);
    }
  });

  it("rejects a declared demo origin with whitespace instead of trimming it", () => {
    for (const declared of [
      " https://demo-staging.example.test",
      "https://demo-staging.example.test ",
      "https://demo-staging.example.test\n",
      "\thttps://demo-staging.example.test"
    ]) {
      expect(publicDemoRuntimeEnabled({ ...staging, APPSELL_PUBLIC_DEMO_ORIGIN: declared })).toBe(false);
      expect(publicDemoRuntimeEnabled({
        ...staging,
        APPSELL_PUBLIC_ORIGIN: declared,
        APPSELL_PUBLIC_DEMO_ORIGIN: declared
      })).toBe(false);
    }
  });

  it("requires the declared demo origin over HTTPS and equal to the public origin", () => {
    expect(publicDemoRuntimeEnabled({ ...staging, APPSELL_PUBLIC_DEMO_ORIGIN: "" })).toBe(false);
    expect(publicDemoRuntimeEnabled({
      ...staging,
      APPSELL_PUBLIC_ORIGIN: "http://demo-staging.example.test",
      APPSELL_PUBLIC_DEMO_ORIGIN: "http://demo-staging.example.test"
    })).toBe(false);
    expect(publicDemoRuntimeEnabled({
      ...staging,
      APPSELL_PUBLIC_DEMO_ORIGIN: "https://other-staging.example.test"
    })).toBe(false);
  });
});
