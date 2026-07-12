import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const previewScript = readFileSync(
  new URL("../scripts/start-local-demo-preview.sh", import.meta.url),
  "utf8"
);

describe("local demo preview bootstrap", () => {
  it("configures the complete host-side API tuple before auditing storage", () => {
    const audit = previewScript.indexOf("python -m appsell_api.storage_public_audit");
    expect(audit).toBeGreaterThan(0);

    for (const variable of [
      "APPSELL_API_INTERNAL_ISSUER",
      "APPSELL_API_INTERNAL_AUDIENCE",
      "APPSELL_API_INTERNAL_HMAC_KEYRING_B64_JSON",
      "APPSELL_API_CONTEXT_HMAC_KEY_B64",
      "APPSELL_API_SERVICE_CREDENTIAL_B64",
      "APPSELL_API_IDENTITY_ISSUER",
      "APPSELL_API_STORAGE_CONTROL_ACCESS_KEY",
      "APPSELL_API_STORAGE_CONTROL_SECRET_KEY",
      "APPSELL_API_STORAGE_ALLOWED_ORIGIN"
    ]) {
      const configured = previewScript.indexOf(`export ${variable}=`);
      expect(configured, `${variable} must be exported before the public audit`).toBeGreaterThan(0);
      expect(configured, `${variable} must be exported before the public audit`).toBeLessThan(audit);
    }
  });
});
