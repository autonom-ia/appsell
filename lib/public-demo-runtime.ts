export const PUBLIC_DEMO_STAGING_RUNTIME_MARKER = "sell-autonomia-native-90-staging";
export const PUBLIC_DEMO_RUNTIME_MARKER = PUBLIC_DEMO_STAGING_RUNTIME_MARKER;

export function publicDemoRuntimeEnabled(
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  return environment.APPSELL_PUBLIC_DEMO_RUNTIME === PUBLIC_DEMO_STAGING_RUNTIME_MARKER &&
    environment.APPSELL_PUBLIC_ORIGIN === "https://sell-issue90.85-31-60-100.sslip.io" &&
    environment.NODE_ENV === "production" &&
    environment.APP_ENV === "ci" &&
    environment.APPSELL_OAUTH_MODE === "local" &&
    environment.APPSELL_DEMO_ENABLED === "true" &&
    environment.APPSELL_IDENTITY_DEMO_ENABLED === "false" &&
    environment.APPSELL_DEMO_SUPERADMIN_ENABLED === "true" &&
    environment.APPSELL_REDIS_PREFIX === "appsell:native:issue90" &&
    environment.APPSELL_IDENTITY_ISSUER === "https://identity.native.local" &&
    environment.APPSELL_INTERNAL_ISSUER === "appsell-bff-native-90" &&
    environment.APPSELL_INTERNAL_AUDIENCE === "appsell-api-native-90" &&
    environment.APPSELL_DOMAIN_API_URL === "http://sell-autonomia_api:8000" &&
    environment.APPSELL_LOCAL_IDENTITY_EMAIL === "runtime.native@autonomia.site";
}
