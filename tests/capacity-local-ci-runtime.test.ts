import { describe, expect, it } from "vitest";

import {
  CAPACITY_LOCAL_CI_RUNTIME,
  capacityLocalCiRuntimeEnabled
} from "../lib/capacity-local-ci-runtime";

const valid = {
  NODE_ENV: "production",
  APPSELL_ENVIRONMENT: "ci",
  APPSELL_CAPACITY_BFF_RUNTIME: "production",
  APPSELL_CAPACITY_LOCAL_CI_RUNTIME: CAPACITY_LOCAL_CI_RUNTIME,
  APPSELL_IDENTITY_ISSUER: "https://identity.capacity.test.autonomia.site",
  APPSELL_INTERNAL_ISSUER: "appsell-bff-capacity",
  APPSELL_INTERNAL_AUDIENCE: "appsell-api-capacity",
  APPSELL_REDIS_PREFIX: "appsell:test:capacity-123-456",
  APPSELL_PUBLIC_ORIGIN: "http://127.0.0.1:3100",
  APPSELL_DOMAIN_API_URL: "http://127.0.0.1:8100"
};

describe("capacity Local/CI production runtime guard", () => {
  it("accepts only the exact production-on-loopback capacity contract", () => {
    expect(capacityLocalCiRuntimeEnabled(valid)).toBe(true);
  });

  it.each([
    ["production environment", { ...valid, APPSELL_ENVIRONMENT: "production" }],
    ["marker", { ...valid, APPSELL_CAPACITY_LOCAL_CI_RUNTIME: "true" }],
    ["BFF runtime", { ...valid, APPSELL_CAPACITY_BFF_RUNTIME: "development" }],
    ["identity issuer", { ...valid, APPSELL_IDENTITY_ISSUER: "https://identity.autonomia.site" }],
    ["internal issuer", { ...valid, APPSELL_INTERNAL_ISSUER: "appsell-bff" }],
    ["Redis namespace", { ...valid, APPSELL_REDIS_PREFIX: "appsell:prod:capacity-123-456" }],
    ["public HTTPS", { ...valid, APPSELL_PUBLIC_ORIGIN: "https://127.0.0.1:3100" }],
    ["public localhost", { ...valid, APPSELL_PUBLIC_ORIGIN: "http://localhost:3100" }],
    ["public privileged port", { ...valid, APPSELL_PUBLIC_ORIGIN: "http://127.0.0.1:80" }],
    ["public path", { ...valid, APPSELL_PUBLIC_ORIGIN: "http://127.0.0.1:3100/path" }],
    ["remote API", { ...valid, APPSELL_DOMAIN_API_URL: "http://10.0.0.2:8100" }]
  ])("rejects a changed %s boundary", (_name, environment) => {
    expect(capacityLocalCiRuntimeEnabled(environment)).toBe(false);
  });
});
