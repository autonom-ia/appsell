import { oauthService } from "@/lib/oauth-service.server";
import { storageUploadConfigurationReady } from "@/lib/storage-upload-policy.server";

export type ReadinessState = {
  ready: boolean;
  redis: boolean;
  api: boolean;
  storageUpload: boolean;
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
  return { ready: redis && api && storageUpload, redis, api, storageUpload };
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
