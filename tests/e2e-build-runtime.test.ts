import { randomBytes } from "node:crypto";

import { describe, expect, it } from "vitest";

import { demoModeEnabled } from "../lib/demo-config";
import { canAccessDemoPreview } from "../lib/demo-view";
import {
  E2E_BUILD_RUNTIME_MARKER,
  e2eBuildRuntimeEnabled
} from "../lib/e2e-build-runtime";
import { oauthRuntimeMode } from "../lib/oauth-runtime-mode";
import { configuredPublicOrigin } from "../lib/public-origin";
import { LocalAesKeyEnvelopeProvider } from "../lib/token-vault";

// O contrato que o job de e2e passa ao `next start` (playwright.config.ts).
// As três travas: NODE_ENV=production com APP_ENV=ci; origem
// http://localhost:3000 com OAuth local; e o marcador do job.
const e2eBuild = {
  NODE_ENV: "production",
  APP_ENV: "ci",
  APPSELL_PUBLIC_ORIGIN: "http://localhost:3000",
  APPSELL_OAUTH_MODE: "local",
  APPSELL_E2E_BUILD_RUNTIME: E2E_BUILD_RUNTIME_MARKER,
  APPSELL_DEMO_ENABLED: "true"
};

const keyring = JSON.stringify([
  { id: "e2e-v1", key: randomBytes(32).toString("base64"), active: true }
]);

function vaultFrom(environment: Partial<Record<string, string>>) {
  return () => LocalAesKeyEnvelopeProvider.fromEnvironment({
    ...environment,
    APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: keyring
  });
}

function without(key: keyof typeof e2eBuild) {
  const copy: Partial<Record<string, string>> = { ...e2eBuild };
  delete copy[key];
  return copy;
}

describe("runtime de demo do e2e sobre o build de produção", () => {
  it("liga só com as três travas juntas", () => {
    expect(e2eBuildRuntimeEnabled(e2eBuild)).toBe(true);
    expect(demoModeEnabled(e2eBuild)).toBe(true);
    expect(oauthRuntimeMode(e2eBuild)).toBe("local");
    expect(canAccessDemoPreview("demo-admin-v1", "production", e2eBuild)).toBe(true);
    expect(configuredPublicOrigin(e2eBuild)).toBe("http://localhost:3000");
    expect(vaultFrom(e2eBuild)).not.toThrow();
  });

  it("sem o marcador do job, o build de produção não liga a demo", () => {
    const semMarcador = without("APPSELL_E2E_BUILD_RUNTIME");
    expect(e2eBuildRuntimeEnabled(semMarcador)).toBe(false);
    expect(demoModeEnabled(semMarcador)).toBe(false);
    expect(() => oauthRuntimeMode(semMarcador)).toThrow("Local OAuth is forbidden in production.");
    expect(canAccessDemoPreview("demo-admin-v1", "production", semMarcador)).toBe(false);
    expect(() => configuredPublicOrigin(semMarcador)).toThrow("Production public origin must use HTTPS.");
    expect(vaultFrom(semMarcador)).toThrow("Local vault key provider is forbidden in production.");
  });

  it("com as variáveis de produção, nada liga a demo, nem com o marcador copiado", () => {
    const producao = {
      NODE_ENV: "production",
      APP_ENV: "production",
      APPSELL_PUBLIC_ORIGIN: "https://sell.autonomia.site",
      APPSELL_OAUTH_MODE: "identity"
    };
    expect(e2eBuildRuntimeEnabled(producao)).toBe(false);
    expect(demoModeEnabled({ ...producao, APPSELL_DEMO_ENABLED: "true" })).toBe(false);
    expect(e2eBuildRuntimeEnabled({ ...producao, APPSELL_E2E_BUILD_RUNTIME: E2E_BUILD_RUNTIME_MARKER })).toBe(false);
    expect(demoModeEnabled({
      ...producao,
      APPSELL_DEMO_ENABLED: "true",
      APPSELL_E2E_BUILD_RUNTIME: E2E_BUILD_RUNTIME_MARKER
    })).toBe(false);
  });

  it.each([
    ["NODE_ENV fora de produção", { ...e2eBuild, NODE_ENV: "development" }],
    ["APP_ENV de produção", { ...e2eBuild, APP_ENV: "production" }],
    ["APP_ENV ausente", without("APP_ENV")],
    ["marcador diferente", { ...e2eBuild, APPSELL_E2E_BUILD_RUNTIME: "true" }],
    ["origem pública real", { ...e2eBuild, APPSELL_PUBLIC_ORIGIN: "https://sell.autonomia.site" }],
    ["origem em outro host", { ...e2eBuild, APPSELL_PUBLIC_ORIGIN: "http://127.0.0.1:3000" }],
    ["origem em outra porta", { ...e2eBuild, APPSELL_PUBLIC_ORIGIN: "http://localhost:3001" }],
    ["origem com caminho", { ...e2eBuild, APPSELL_PUBLIC_ORIGIN: "http://localhost:3000/x" }],
    ["origem ausente", without("APPSELL_PUBLIC_ORIGIN")],
    ["OAuth pelo Identity", { ...e2eBuild, APPSELL_OAUTH_MODE: "identity" }],
    ["OAuth ausente", without("APPSELL_OAUTH_MODE")]
  ])("recusa quando muda %s", (_nome, environment) => {
    expect(e2eBuildRuntimeEnabled(environment)).toBe(false);
  });
});
