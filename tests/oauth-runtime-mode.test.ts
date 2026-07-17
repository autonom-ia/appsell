import { describe, expect, it } from "vitest";

import { OAuthRuntimeConfigurationError, oauthRuntimeMode } from "../lib/oauth-runtime-mode";

describe("OAuth runtime mode", () => {
  it("defaults to the local provider outside production", () => {
    expect(oauthRuntimeMode({ NODE_ENV: "development" })).toBe("local");
    expect(oauthRuntimeMode({ NODE_ENV: "test" })).toBe("local");
  });

  it("requires an explicit real provider in production", () => {
    expect(() => oauthRuntimeMode({ NODE_ENV: "production" }))
      .toThrow(OAuthRuntimeConfigurationError);
    expect(() => oauthRuntimeMode({ NODE_ENV: "production", APPSELL_OAUTH_MODE: "local" }))
      .toThrow("Local OAuth is forbidden in production");
    expect(oauthRuntimeMode({ NODE_ENV: "production", APPSELL_OAUTH_MODE: "identity" }))
      .toBe("identity");
  });

  it("rejects unknown modes", () => {
    expect(() => oauthRuntimeMode({ NODE_ENV: "test", APPSELL_OAUTH_MODE: "fallback" }))
      .toThrow("APPSELL_OAUTH_MODE must be local or identity");
  });
});
