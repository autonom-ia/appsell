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

  it("keeps draft update and publication tokens on distinct stable actions", () => {
    const key = randomBytes(32);
    const updateAction = "PATCH:/api/products";
    const publishAction = "POST:/api/products/publications";
    const update = deriveCsrfToken("opaque-session-a", updateAction, key);
    const publish = deriveCsrfToken("opaque-session-a", publishAction, key);
    expect(update).not.toBe(publish);
    expect(verifyCsrfToken(update, "opaque-session-a", updateAction, key)).toBe(true);
    expect(verifyCsrfToken(update, "opaque-session-a", publishAction, key)).toBe(false);
  });

  it("keeps curriculum mutation classes on distinct stable actions", () => {
    const key = randomBytes(32);
    const actions = [
      "POST:/api/curriculum",
      "PATCH:/api/curriculum",
      "PUT:/api/curriculum/order",
      "POST:/api/curriculum/archive"
    ];
    const tokens = actions.map((action) => deriveCsrfToken("opaque-session-a", action, key));
    expect(new Set(tokens).size).toBe(actions.length);
    for (let index = 0; index < actions.length; index += 1) {
      expect(verifyCsrfToken(tokens[index], "opaque-session-a", actions[index], key)).toBe(true);
      expect(verifyCsrfToken(tokens[index], "opaque-session-a", actions[(index + 1) % actions.length], key)).toBe(false);
    }
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
