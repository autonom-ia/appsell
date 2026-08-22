const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
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

  let url: URL;
  try {
    url = new URL(uploadUrl);
  } catch {
    return null;
  }
  const expiresMs = Date.parse(expiresAt);
  if (
    url.origin !== storageOrigin ||
    url.username ||
    url.password ||
    url.hash ||
    !url.searchParams.has("X-Amz-Signature") ||
    !Number.isFinite(expiresMs) ||
    expiresMs <= nowMs ||
    expiresMs > nowMs + 330_000
  ) return null;

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
