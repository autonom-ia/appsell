export const PUBLIC_DEMO_RUNTIME_MARKER = "sell-autonomia-runtime-preview-77";

export function publicDemoRuntimeEnabled(
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  return environment.NODE_ENV === "production" &&
    environment.APP_ENV === "ci" &&
    environment.APPSELL_PUBLIC_DEMO_RUNTIME === PUBLIC_DEMO_RUNTIME_MARKER &&
    environment.APPSELL_PUBLIC_ORIGIN === "https://sell.autonomia.site" &&
    environment.APPSELL_OAUTH_MODE === "local" &&
    environment.APPSELL_DEMO_ENABLED === "true" &&
    environment.APPSELL_IDENTITY_DEMO_ENABLED === "false" &&
    environment.APPSELL_DEMO_SUPERADMIN_ENABLED === "true" &&
    environment.APPSELL_REDIS_PREFIX === "appsell:runtime-preview:issue77" &&
    environment.APPSELL_IDENTITY_ISSUER === "https://identity.runtime-preview.local" &&
    environment.APPSELL_INTERNAL_ISSUER === "appsell-bff-runtime-preview-77" &&
    environment.APPSELL_INTERNAL_AUDIENCE === "appsell-api-runtime-preview-77" &&
    environment.APPSELL_DOMAIN_API_URL === "http://domain-api:15800" &&
    environment.APPSELL_LOCAL_IDENTITY_EMAIL === "creator.runtime-preview@autonomia.site";
}
