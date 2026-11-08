export const PUBLIC_DEMO_STAGING_RUNTIME_MARKER = "sell-autonomia-native-90-staging";
export const PUBLIC_DEMO_RUNTIME_MARKER = PUBLIC_DEMO_STAGING_RUNTIME_MARKER;

// O endereço público da homologação vem da env, nunca de literal no código: o
// repositório é público. A demo só liga quando a origem pública do web é a
// origem declarada para a demo, por HTTPS, e nunca a origem de produção.
const PRODUCTION_PUBLIC_ORIGIN = "https://sell.autonomia.site";

function demoOriginMatches(environment: Partial<Record<string, string | undefined>>) {
  const declared = environment.APPSELL_PUBLIC_DEMO_ORIGIN?.trim() ?? "";
  return declared.startsWith("https://") &&
    declared !== PRODUCTION_PUBLIC_ORIGIN &&
    environment.APPSELL_PUBLIC_ORIGIN === declared;
}

export function publicDemoRuntimeEnabled(
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  return environment.APPSELL_PUBLIC_DEMO_RUNTIME === PUBLIC_DEMO_STAGING_RUNTIME_MARKER &&
    demoOriginMatches(environment) &&
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
