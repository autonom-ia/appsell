export const CAPACITY_LOCAL_CI_RUNTIME = "synthetic-loopback-v1";

export function capacityLocalCiRuntimeEnabled(
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  return environment.NODE_ENV === "production" &&
    environment.APPSELL_ENVIRONMENT === "ci" &&
    environment.APPSELL_CAPACITY_BFF_RUNTIME === "production" &&
    environment.APPSELL_CAPACITY_LOCAL_CI_RUNTIME === CAPACITY_LOCAL_CI_RUNTIME &&
    environment.APPSELL_IDENTITY_ISSUER === "https://identity.capacity.test.autonomia.site" &&
    environment.APPSELL_INTERNAL_ISSUER === "appsell-bff-capacity" &&
    environment.APPSELL_INTERNAL_AUDIENCE === "appsell-api-capacity" &&
    /^appsell:test:capacity-[0-9]+-[0-9]+$/.test(environment.APPSELL_REDIS_PREFIX ?? "") &&
    loopbackHttpOrigin(environment.APPSELL_PUBLIC_ORIGIN) &&
    loopbackHttpOrigin(environment.APPSELL_DOMAIN_API_URL);
}

function loopbackHttpOrigin(value: string | undefined) {
  if (!value) return false;
  try {
    const parsed = new URL(value);
    const port = Number(parsed.port);
    return value === parsed.origin &&
      parsed.protocol === "http:" &&
      parsed.hostname === "127.0.0.1" &&
      Number.isSafeInteger(port) &&
      port >= 1_024 &&
      port <= 65_535 &&
      !parsed.username &&
      !parsed.password &&
      parsed.pathname === "/" &&
      !parsed.search &&
      !parsed.hash;
  } catch {
    return false;
  }
}
