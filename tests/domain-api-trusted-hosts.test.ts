import { describe, expect, it } from "vitest";

import {
  trustedInternalApiHost,
  TrustedInternalHostsConfigurationError
} from "../lib/domain-api-trusted-hosts";

describe("domain API trusted internal hosts", () => {
  it("fails closed when the allowlist is not configured", () => {
    expect(trustedInternalApiHost("sell-autonomia_api", {})).toBe(false);
    expect(
      trustedInternalApiHost("sell-autonomia_api", {
        APPSELL_DOMAIN_API_TRUSTED_INTERNAL_HOSTS: ""
      })
    ).toBe(false);
  });

  it("trusts only hostnames explicitly listed", () => {
    const environment = {
      APPSELL_DOMAIN_API_TRUSTED_INTERNAL_HOSTS: "sell-autonomia_api"
    };
    expect(trustedInternalApiHost("sell-autonomia_api", environment)).toBe(true);
    expect(trustedInternalApiHost("other-service", environment)).toBe(false);
    expect(trustedInternalApiHost("sell-autonomia_api2", environment)).toBe(false);
  });

  it("supports multiple comma-separated entries with surrounding whitespace", () => {
    const environment = {
      APPSELL_DOMAIN_API_TRUSTED_INTERNAL_HOSTS: " sell-autonomia_api , appsell-api-blue "
    };
    expect(trustedInternalApiHost("sell-autonomia_api", environment)).toBe(true);
    expect(trustedInternalApiHost("appsell-api-blue", environment)).toBe(true);
    expect(trustedInternalApiHost("appsell-api-green", environment)).toBe(false);
  });

  it.each([
    ["public FQDN", "api.evil.example"],
    ["IPv4 literal", "10.0.0.2"],
    ["uppercase", "Sell-Autonomia_API"],
    ["empty entry from trailing comma", "sell-autonomia_api,"],
    ["whitespace-only value", "   "],
    ["leading hyphen", "-internal"],
    ["scheme smuggling", "http://sell-autonomia_api"],
    ["port in entry", "sell-autonomia_api:8000"]
  ])("rejects a %s allowlist entry at configuration time", (_name, value) => {
    expect(() =>
      trustedInternalApiHost("sell-autonomia_api", {
        APPSELL_DOMAIN_API_TRUSTED_INTERNAL_HOSTS: value
      })
    ).toThrow(TrustedInternalHostsConfigurationError);
  });

  it("never trusts a dotted hostname because entries cannot contain dots", () => {
    expect(
      trustedInternalApiHost("sell-autonomia.api", {
        APPSELL_DOMAIN_API_TRUSTED_INTERNAL_HOSTS: "sell-autonomia_api"
      })
    ).toBe(false);
  });
});
