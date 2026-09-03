const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const COVER_MAX_BYTES = 5 * 1024 * 1024;
const ALLOWED_HEADERS = new Set([
  "content-type",
  "x-amz-checksum-sha256",
  "x-amz-meta-appsell-upload-id"
]);

export class StorageUploadConfigurationError extends Error {}

/** Os cabeçalhos de uma parte: só o hash dela. Tipo e identificação do envio
 * pertencem ao objeto, e são gravados na abertura, não em cada parte. */
const ALLOWED_PART_HEADERS = new Set(["x-amz-checksum-sha256"]);

/** Espelha `MULTIPART_PART_BYTES` e `MULTIPART_MAX_PARTS` do domínio. */
const PART_BYTES = 16 * 1024 * 1024;
const MAX_PARTS = 1000;

/** Quanto tempo uma intenção fatiada pode declarar.
 *
 * Duas horas mais folga de relógio. É o prazo que o banco concede para um envio
 * grande: numa linha de 2 Mbit/s meio giga leva trinta e seis minutos, e cortar
 * em cinco seria dizer ao vendedor que ele tem cinco. */
const MAX_MULTIPART_WINDOW_MS = 2 * 60 * 60 * 1000 + 300_000;

export interface BrowserMultipartUploadIntent {
  upload_id: string;
  asset_id: string;
  expires_at: string;
  multipart: { part_bytes: number; part_count: number };
}

export interface BrowserUploadPart {
  part_number: number;
  upload_url: string;
  upload_origin: string;
  expires_at: string;
  required_headers: Record<string, string>;
  required_content_length: number;
}

/**
 * A intenção de um envio fatiado, que não carrega URL nenhuma.
 *
 * Não há endereço para provar aqui, e é justamente por isso que ela é separada:
 * deixar a mesma função aceitar uma intenção com e sem URL abriria a porta para
 * uma resposta sem URL passar pela prova de endereço sem ninguém notar.
 */
export function browserMultipartUploadIntent(
  value: unknown,
  nowMs = Date.now()
): BrowserMultipartUploadIntent | null {
  if (!isRecord(value)) return null;
  const uploadId = value.upload_id;
  const assetId = value.asset_id;
  const expiresAt = value.expires_at;
  const multipart = value.multipart;
  if (
    typeof uploadId !== "string" ||
    typeof assetId !== "string" ||
    typeof expiresAt !== "string" ||
    !UUID.test(uploadId) ||
    !UUID.test(assetId) ||
    !isRecord(multipart) ||
    // Uma URL numa intenção fatiada é resposta que não deveria existir, e
    // aceitá-la em silêncio esconderia a divergência.
    value.upload_url !== undefined ||
    multipart.part_bytes !== PART_BYTES ||
    !Number.isInteger(multipart.part_count) ||
    Number(multipart.part_count) < 2 ||
    Number(multipart.part_count) > MAX_PARTS ||
    !freshEnough(expiresAt, nowMs, MAX_MULTIPART_WINDOW_MS)
  ) return null;
  return {
    upload_id: uploadId,
    asset_id: assetId,
    expires_at: expiresAt,
    multipart: { part_bytes: PART_BYTES, part_count: Number(multipart.part_count) }
  };
}

/**
 * A assinatura de uma parte, com a mesma prova de endereço da intenção única.
 *
 * O tamanho vem junto e é exato: o navegador precisa recusar antes de mandar
 * bytes que o storage rejeitaria, e é ele que faz a barra de progresso
 * significar alguma coisa.
 */
export function browserUploadPart(
  value: unknown,
  appOrigin: string,
  storageOrigin: string,
  nowMs = Date.now()
): BrowserUploadPart | null {
  if (!isRecord(value) || storageOrigin === appOrigin) return null;
  const partNumber = value.part_number;
  const expiresAt = value.expires_at;
  const contentLength = value.required_content_length;
  if (
    !Number.isInteger(partNumber) ||
    Number(partNumber) < 1 ||
    Number(partNumber) > MAX_PARTS ||
    typeof expiresAt !== "string" ||
    !Number.isInteger(contentLength) ||
    Number(contentLength) < 1 ||
    Number(contentLength) > PART_BYTES
  ) return null;
  const url = signedStorageUrl(value.upload_url, storageOrigin);
  const headers = exactHeaders(value.required_headers, ALLOWED_PART_HEADERS);
  if (!url || !headers || !freshEnough(expiresAt, nowMs)) return null;
  return {
    part_number: Number(partNumber),
    upload_url: url.toString(),
    upload_origin: storageOrigin,
    expires_at: expiresAt,
    required_headers: headers,
    required_content_length: Number(contentLength)
  };
}

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

export interface BrowserLogoUploadIntent {
  upload_id: string;
  upload_url: string;
  upload_origin: string;
  expires_at: string;
  required_headers: Record<string, string>;
  required_content_length: number;
}

/** O logo da loja: a prova da capa, sem a versão do produto. */
export function browserLogoUploadIntent(
  value: unknown,
  appOrigin: string,
  storageOrigin: string,
  nowMs = Date.now()
): BrowserLogoUploadIntent | null {
  if (!isRecord(value) || storageOrigin === appOrigin) return null;
  const uploadId = value.upload_id;
  const expiresAt = value.expires_at;
  const contentLength = value.required_content_length;
  if (
    typeof uploadId !== "string" ||
    !UUID.test(uploadId) ||
    typeof expiresAt !== "string" ||
    !Number.isInteger(contentLength) ||
    Number(contentLength) < 1 ||
    Number(contentLength) > COVER_MAX_BYTES
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
    required_content_length: Number(contentLength)
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

function freshEnough(expiresAt: string, nowMs: number, janelaMs = 330_000): boolean {
  const expiresMs = Date.parse(expiresAt);
  return Number.isFinite(expiresMs) && expiresMs > nowMs && expiresMs <= nowMs + janelaMs;
}

function exactUploadHeaders(value: unknown): Record<string, string> | null {
  return exactHeaders(value, ALLOWED_HEADERS);
}

/** Exatamente estes cabeçalhos, nem um a mais: um extra viajaria assinado. */
function exactHeaders(value: unknown, permitidos: Set<string>): Record<string, string> | null {
  if (!isRecord(value)) return null;
  const entries = Object.entries(value);
  const names = entries.map(([name]) => name.toLowerCase());
  if (
    entries.length !== permitidos.size ||
    new Set(names).size !== names.length ||
    ![...permitidos].every((name) => names.includes(name)) ||
    entries.some(([, header]) => typeof header !== "string" || header.length === 0)
  ) return null;
  return Object.fromEntries(entries) as Record<string, string>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
