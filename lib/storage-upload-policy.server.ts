const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const COVER_MAX_BYTES = 5 * 1024 * 1024;
const ALLOWED_HEADERS = new Set([
  "content-type",
  "x-amz-checksum-sha256",
  "x-amz-meta-appsell-upload-id"
]);

export class StorageUploadConfigurationError extends Error {}

export interface BrowserUploadIntent {
  upload_id: string;
  asset_id: string;
  upload_url: string;
  upload_origin: string;
  expires_at: string;
  required_headers: Record<string, string>;
}

export function configuredStorageUploadOrigin(
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  const raw = environment.APPSELL_STORAGE_PUBLIC_ORIGIN ?? "";
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new StorageUploadConfigurationError(
      "APPSELL_STORAGE_PUBLIC_ORIGIN must be an absolute origin."
    );
  }
  if (
    raw !== url.origin ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new StorageUploadConfigurationError(
      "APPSELL_STORAGE_PUBLIC_ORIGIN must contain only scheme and host."
    );
  }
  if (environment.NODE_ENV === "production" && url.protocol !== "https:") {
    throw new StorageUploadConfigurationError(
      "Production storage upload origin must use HTTPS."
    );
  }
  return url.origin;
}

export function storageUploadConfigurationReady(
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  // EasyPanel staging runs Next in production mode while keeping APP_ENV=ci and
  // the domain API storage provider disabled.
  if (environment.APP_ENV !== "production") return true;
  try {
    configuredStorageUploadOrigin(environment);
    return true;
  } catch {
    return false;
  }
}

export interface BrowserCoverUploadIntent {
  upload_id: string;
  upload_url: string;
  upload_origin: string;
  expires_at: string;
  required_headers: Record<string, string>;
  required_content_length: number;
  product_version: number;
}

/**
 * A capa usa a mesma prova de endereço que o material de aula: a URL precisa
 * ser do host de armazenamento, precisa estar assinada, não pode carregar
 * credenciais e não pode ter validade maior do que a assinatura permite. O que
 * muda é só o que acompanha a URL — não há asset, e o tamanho exato vai junto
 * porque o navegador precisa recusar antes de mandar 5 MiB que o S3 rejeitaria.
 */
export function browserCoverUploadIntent(
  value: unknown,
  appOrigin: string,
  storageOrigin: string,
  nowMs = Date.now()
): BrowserCoverUploadIntent | null {
  if (!isRecord(value) || storageOrigin === appOrigin) return null;
  const uploadId = value.upload_id;
  const expiresAt = value.expires_at;
  const contentLength = value.required_content_length;
  const productVersion = value.product_version;
  if (
    typeof uploadId !== "string" ||
    !UUID.test(uploadId) ||
    typeof expiresAt !== "string" ||
    !Number.isInteger(contentLength) ||
    Number(contentLength) < 1 ||
    Number(contentLength) > COVER_MAX_BYTES ||
    !Number.isInteger(productVersion) ||
    Number(productVersion) < 1
  ) return null;

  const url = signedStorageUrl(value.upload_url, storageOrigin);
  const headers = exactUploadHeaders(value.required_headers);
  if (!url || !headers || !freshEnough(expiresAt, nowMs)) return null;
  return {
    upload_id: uploadId,
    upload_url: url.toString(),
    upload_origin: storageOrigin,
    expires_at: expiresAt,
    required_headers: headers,
    required_content_length: Number(contentLength),
    product_version: Number(productVersion)
  };
}

export function browserUploadIntent(
  value: unknown,
  appOrigin: string,
  storageOrigin: string,
  nowMs = Date.now()
): BrowserUploadIntent | null {
  if (!isRecord(value) || storageOrigin === appOrigin) return null;
  const uploadId = value.upload_id;
  const assetId = value.asset_id;
  const uploadUrl = value.upload_url;
  const expiresAt = value.expires_at;
  if (
    typeof uploadId !== "string" ||
    typeof assetId !== "string" ||
    typeof uploadUrl !== "string" ||
    typeof expiresAt !== "string" ||
    !UUID.test(uploadId) ||
    !UUID.test(assetId)
  ) return null;

  const url = signedStorageUrl(uploadUrl, storageOrigin);
  if (!url || !freshEnough(expiresAt, nowMs)) return null;

  const headers = exactUploadHeaders(value.required_headers);
  return headers ? {
    upload_id: uploadId,
    asset_id: assetId,
    upload_url: url.toString(),
    upload_origin: storageOrigin,
    expires_at: expiresAt,
    required_headers: headers
  } : null;
}

/**
 * Deixa passar só a capa cujo endereço aponta para o armazenamento configurado.
 *
 * O navegador vai buscar esta URL sozinho, num `img`. Um endereço de outra
 * origem viraria uma requisição do vendedor para um host que ninguém escolheu —
 * e nem precisa entregar imagem nenhuma para servir de rastreador. Capa que não
 * passa vira ausência de capa, e a tela mostra a arte gerada, que é um estado
 * perfeitamente bom.
 */
export function trustedProductCover(value: unknown, storageOrigin: string): unknown {
  if (!isRecord(value)) return null;
  const width = Number(value.width);
  const height = Number(value.height);
  if (!Number.isSafeInteger(width) || width < 1) return null;
  if (!Number.isSafeInteger(height) || height < 1) return null;
  if (typeof value.media_type !== "string") return null;
  let configuredOrigin: string;
  try {
    configuredOrigin = configuredStorageUploadOrigin({
      ...process.env,
      APPSELL_STORAGE_PUBLIC_ORIGIN: storageOrigin
    });
  } catch {
    return null;
  }
  return signedStorageUrl(value.url, configuredOrigin) ? value : null;
}

export function trustedStorageDownloadUrl(
  value: unknown,
  expires: unknown,
  storageOrigin: string,
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  if (
    typeof value !== "string" ||
    !Number.isInteger(expires) ||
    Number(expires) < 15 ||
    Number(expires) > 60
  ) return false;
  try {
    const configuredOrigin = configuredStorageUploadOrigin({
      ...environment,
      APPSELL_STORAGE_PUBLIC_ORIGIN: storageOrigin
    });
    const url = new URL(value);
    return url.origin === configuredOrigin
      && !url.username
      && !url.password
      && !url.hash
      && url.searchParams.has("X-Amz-Signature");
  } catch {
    return false;
  }
}

function signedStorageUrl(value: unknown, storageOrigin: string): URL | null {
  if (typeof value !== "string") return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (
    url.origin !== storageOrigin ||
    url.username ||
    url.password ||
    url.hash ||
    !url.searchParams.has("X-Amz-Signature")
  ) return null;
  return url;
}

function freshEnough(expiresAt: string, nowMs: number): boolean {
  const expiresMs = Date.parse(expiresAt);
  return Number.isFinite(expiresMs) && expiresMs > nowMs && expiresMs <= nowMs + 330_000;
}

function exactUploadHeaders(value: unknown): Record<string, string> | null {
  if (!isRecord(value)) return null;
  const entries = Object.entries(value);
  const names = entries.map(([name]) => name.toLowerCase());
  if (
    entries.length !== ALLOWED_HEADERS.size ||
    new Set(names).size !== names.length ||
    ![...ALLOWED_HEADERS].every((name) => names.includes(name)) ||
    entries.some(([, header]) => typeof header !== "string" || header.length === 0)
  ) return null;
  return Object.fromEntries(entries) as Record<string, string>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
