import { randomBytes } from "node:crypto";

import { describe, expect, it } from "vitest";

import {
  CsrfConfigurationError,
  deriveCsrfToken,
  parseCsrfKey,
  verifyCsrfToken
} from "../lib/csrf";
import {
  configuredPublicOrigin,
  hasConfiguredOrigin,
  PublicOriginConfigurationError
} from "../lib/public-origin";

describe("mutation protection", () => {
  it("binds a CSRF token to the opaque session and exact action", () => {
    const key = randomBytes(32);
    const token = deriveCsrfToken("opaque-session-a", "POST:/auth/logout", key);

    expect(verifyCsrfToken(token, "opaque-session-a", "POST:/auth/logout", key)).toBe(true);
    expect(verifyCsrfToken(token, "opaque-session-b", "POST:/auth/logout", key)).toBe(false);
    expect(verifyCsrfToken(token, "opaque-session-a", "POST:/api/onboarding", key)).toBe(false);
    expect(verifyCsrfToken(null, "opaque-session-a", "POST:/auth/logout", key)).toBe(false);
  });

  it("requires a canonical independent 32-byte key", () => {
    const encoded = randomBytes(32).toString("base64");
    const key = parseCsrfKey(encoded, []);
    expect(key).toHaveLength(32);
    expect(() => parseCsrfKey(encoded, [key])).toThrow(CsrfConfigurationError);
    expect(() => parseCsrfKey(randomBytes(31).toString("base64"), [])).toThrow(CsrfConfigurationError);
    expect(() => parseCsrfKey(encoded.replace(/=$/, ""), [])).toThrow(CsrfConfigurationError);
  });

  it("uses only the configured public origin as authority", () => {
    const expected = configuredPublicOrigin({
      APPSELL_PUBLIC_ORIGIN: "https://sell.autonomia.site",
      NODE_ENV: "production"
    });
    expect(hasConfiguredOrigin(new Request("https://attacker.invalid/path", {
      headers: { origin: expected, host: "attacker.invalid" }
    }), expected)).toBe(true);
    for (const origin of [null, "null", "https://evil.test", "https://admin.autonomia.site"]) {
      const headers = new Headers();
      if (origin !== null) headers.set("origin", origin);
      expect(hasConfiguredOrigin(new Request("https://sell.autonomia.site/path", { headers }), expected)).toBe(false);
    }
  });

  it("rejects non-origin values and insecure production origins", () => {
    expect(() => configuredPublicOrigin({ APPSELL_PUBLIC_ORIGIN: "https://sell.autonomia.site/path" }))
      .toThrow(PublicOriginConfigurationError);
    expect(() => configuredPublicOrigin({
      APPSELL_PUBLIC_ORIGIN: "http://sell.autonomia.site",
      NODE_ENV: "production"
    })).toThrow(PublicOriginConfigurationError);
  });
});
