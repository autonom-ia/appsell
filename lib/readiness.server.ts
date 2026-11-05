import { checkoutProductMapIsValid } from "@/lib/checkout-config";
import { checkoutProvider } from "@/lib/financial-external-checkout.server";
import { oauthService } from "@/lib/oauth-service.server";
import { storageUploadConfigurationReady } from "@/lib/storage-upload-policy.server";

export type ReadinessState = {
  ready: boolean;
  redis: boolean;
  api: boolean;
  storageUpload: boolean;
  checkoutMap: boolean;
};

/**
 * O estado das dependências do `web`, compartilhado por `ready` e `startup`.
 *
 * Os dois endereços precisam concordar sobre o que "pronto" significa: o
 * `startup` espelha este cálculo enquanto a task nunca ficou pronta, e é ele
 * que o `HEALTHCHECK` do contêiner sonda.
 */
export async function readinessState(): Promise<ReadinessState> {
  const [redis, api, storageUpload] = await Promise.all([
    redisReady(),
    domainApiReady(),
    storageUploadConfigurationReady()
  ]);
  const checkoutMap = checkoutProductMapReady();
  return {
    ready: redis && api && storageUpload && checkoutMap,
    redis,
    api,
    storageUpload,
    checkoutMap
  };
}

/**
 * Com o checkout do Stripe local, um mapa de produtos que não parseia derruba
 * todo pagamento: o webhook não acha produto nenhum (#148). Reprovar aqui
 * impede o deploy de promover a task, em vez de cada comprador descobrir.
 *
 * Com `financial_checkout` o mapa não decide venda nem entrega, então ele não
 * pode tirar do ar uma loja que vende por lá.
 */
function checkoutProductMapReady() {
  return checkoutProvider() === "financial_checkout" || checkoutProductMapIsValid();
}

async function redisReady() {
  try {
    return await oauthService().health();
  } catch {
    return false;
  }
}

async function domainApiReady() {
  try {
    const raw = process.env.APPSELL_DOMAIN_API_URL ?? "";
    const origin = new URL(raw);
    if (raw !== origin.origin || origin.username || origin.password) return false;
    const response = await fetch(new URL("/health/ready", origin), {
      cache: "no-store",
      signal: AbortSignal.timeout(2_000)
    });
    if (!response.ok) return false;
    const payload = await response.json() as unknown;
    return isReadyPayload(payload);
  } catch {
    return false;
  }
}

function isReadyPayload(value: unknown) {
  return typeof value === "object" && value !== null && !Array.isArray(value) &&
    (value as Record<string, unknown>).status === "ok" &&
    (value as Record<string, unknown>).database === true;
}
