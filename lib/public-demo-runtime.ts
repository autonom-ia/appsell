export const PUBLIC_DEMO_STAGING_RUNTIME_MARKER = "sell-autonomia-native-90-staging";
export const PUBLIC_DEMO_RUNTIME_MARKER = PUBLIC_DEMO_STAGING_RUNTIME_MARKER;

// O endereço público da homologação vem da env, nunca de literal no código: o
// repositório é público. A demo só liga quando a origem pública do web é a
// origem declarada para a demo, por HTTPS, e nunca o domínio de produção.
//
// A origem declarada precisa estar na forma canônica que o parser de URL
// devolve (`url.origin`): sem maiúscula, barra final, `:443`, userinfo,
// caminho ou espaço. Assim, uma grafia alternativa da produção não passa
// pela comparação. O valor não é aparado: espaço na env é recusado.
const PRODUCTION_DOMAIN = "autonomia.site";

function parseCanonicalHttpsOrigin(value: string): URL | null {
  if (value === "" || value.trim() !== value || value.includes(" ")) return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || url.origin !== value) return null;
  return url;
}

function isProductionHostname(hostname: string) {
  const host = hostname.toLowerCase();
  return host === PRODUCTION_DOMAIN || host.endsWith(`.${PRODUCTION_DOMAIN}`);
}

function demoOriginMatches(environment: Partial<Record<string, string | undefined>>) {
  const declared = environment.APPSELL_PUBLIC_DEMO_ORIGIN ?? "";
  const url = parseCanonicalHttpsOrigin(declared);
  if (url === null) return false;
  if (url.hostname.endsWith(".")) return false;
  if (isProductionHostname(url.hostname)) return false;
  return environment.APPSELL_PUBLIC_ORIGIN === declared;
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
