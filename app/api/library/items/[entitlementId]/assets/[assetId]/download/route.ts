import { Readable } from "node:stream";
import { NextRequest, NextResponse } from "next/server";

import { authorizeDomainLearningAssetDownload } from "@/lib/internal-api-client.server";
import { assetMediaSpec, assetSeedKey, type AssetMediaType } from "@/lib/asset-media";
import {
  configuredPrivateLearningAssetRoot,
  prepareVerifiedPrivateLearningAsset
} from "@/lib/private-learning-asset.server";
import {
  authenticatedLearningSession,
  learningAssetIds,
  learningError,
  learningNotFound,
  learningUnauthorized,
  type LearningAssetRouteContext
} from "../../../../_route-shared";
import { configuredPublicOrigin } from "@/lib/public-origin";
import {
  configuredStorageUploadOrigin,
  trustedStorageDownloadUrl
} from "@/lib/storage-upload-policy.server";

const SHA256 = /^[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

interface DownloadMetadata {
  id: string;
  delivery: "legacy_bundle";
  seed_key: string;
  file_name: string;
  title: string;
  media_type: AssetMediaType;
  byte_size: number;
  sha256: string;
}

interface RedirectDownloadMetadata {
  id: string;
  delivery: "redirect";
  download_url: string;
  expires_in_seconds: number;
  file_name: string;
  title: string;
  media_type: AssetMediaType;
  byte_size: number;
  sha256: string;
}

export async function GET(request: NextRequest, context: LearningAssetRouteContext) {
  try {
    const authenticated = await authenticatedLearningSession(request);
    if (!authenticated) return learningUnauthorized();
    const ids = await learningAssetIds(context);
    if (!ids) return learningNotFound();
    const authorization = await authorizeDomainLearningAssetDownload(
      authenticated.sessionId,
      authenticated.session,
      ids.entitlementId,
      ids.assetId
    );
    const metadata = authorizedDownloadMetadata(authorization, ids.assetId);
    if (!metadata) return downloadUnavailable("asset_metadata_invalid");
    if (metadata.delivery === "redirect") return redirectDownload(metadata.download_url);
    const asset = await prepareVerifiedPrivateLearningAsset(
      metadata,
      configuredPrivateLearningAssetRoot()
    );
    if (!asset) return downloadUnavailable("asset_integrity_failure");
    return new NextResponse(
      Readable.toWeb(asset.handle.createReadStream({ autoClose: true })) as ReadableStream<Uint8Array>,
      { status: 200, headers: downloadHeaders(metadata, asset.size) }
    );
  } catch (error) {
    return learningError(error);
  }
}

function authorizedDownloadMetadata(
  value: unknown,
  expectedAssetId: string
): DownloadMetadata | RedirectDownloadMetadata | null {
  if (!isRecord(value) || !hasExactKeys(value, ["asset"]) || !isRecord(value.asset)) {
    return null;
  }
  const asset = value.asset;
  // O download entrega o arquivo inteiro, então o formato tem de ser baixável —
  // é o que mantém o MP4 fora desta rota. O teto vem do catálogo, por formato:
  // o número fixo de 10 MiB que estava aqui ficou para trás quando a #287 levou
  // o PDF para 100 MiB, e passou a recusar PDF legítimo.
  const spec = assetMediaSpec(asset.media_type);
  const commonValid = asset.id === expectedAssetId && typeof asset.id === "string"
    && UUID.test(asset.id) && spec !== null && spec.downloadable
    && validDownloadFile(asset.file_name, spec.extension)
    && typeof asset.title === "string" && asset.title.length >= 2
    && Number.isInteger(asset.byte_size) && Number(asset.byte_size) >= 1
    && Number(asset.byte_size) <= spec.maxBytes
    && typeof asset.sha256 === "string" && SHA256.test(asset.sha256);
  if (!commonValid) return null;
  if (asset.delivery === "legacy_bundle" && hasExactKeys(asset, [
    "byte_size", "delivery", "file_name", "id", "media_type", "seed_key", "sha256", "title"
  ]) && asset.seed_key === assetSeedKey(asset.file_name)) {
    return asset as unknown as DownloadMetadata;
  }
  if (asset.delivery === "redirect" && hasExactKeys(asset, [
    "byte_size", "delivery", "download_url", "expires_in_seconds", "file_name", "id",
    "media_type", "sha256", "title"
  ]) && validDownloadUrl(asset.download_url, asset.expires_in_seconds)) {
    return asset as unknown as RedirectDownloadMetadata;
  }
  return null;
}

function redirectDownload(url: string) {
  const response = NextResponse.redirect(url, 303);
  for (const [name, value] of Object.entries(noStoreHeaders())) response.headers.set(name, value);
  return response;
}

function downloadHeaders(metadata: DownloadMetadata, size: number) {
  return {
    ...noStoreHeaders(),
    "accept-ranges": "none",
    "content-disposition": attachmentContentDisposition(metadata.file_name),
    "content-length": String(size),
    "content-security-policy": "sandbox",
    "content-type": metadata.media_type,
    "cross-origin-resource-policy": "same-origin"
  };
}

function noStoreHeaders() {
  return {
    "cache-control": "private, no-store, max-age=0",
    pragma: "no-cache",
    "referrer-policy": "no-referrer",
    "surrogate-control": "no-store",
    vary: "Cookie",
    "x-content-type-options": "nosniff"
  };
}

function attachmentContentDisposition(fileName: string) {
  const asciiFallback = fileName.normalize("NFKD")
    .replace(/[^\x20-\x7e]/g, "_")
    .replace(/["\\]/g, "_");
  const encoded = encodeURIComponent(fileName).replace(/[!'()*]/g, (character) =>
    `%${character.charCodeAt(0).toString(16).toUpperCase()}`);
  return `attachment; filename="${asciiFallback}"; filename*=UTF-8''${encoded}`;
}

function validDownloadFile(value: unknown, extension: string) {
  return typeof value === "string" && Buffer.byteLength(value) <= 160
    && value === value.trim() && value.toLowerCase().endsWith(`.${extension}`)
    && !/[\\/\u0000-\u001f\u007f]/.test(value);
}

function validDownloadUrl(value: unknown, expires: unknown) {
  try {
    const storageOrigin = configuredStorageUploadOrigin();
    const appOrigin = new URL(configuredPublicOrigin());
    return storageOrigin !== appOrigin.origin
      && trustedStorageDownloadUrl(value, expires, storageOrigin);
  } catch {
    return false;
  }
}

function downloadUnavailable(error: string) {
  return NextResponse.json({ error }, { status: 503, headers: noStoreHeaders() });
}

function hasExactKeys(value: Record<string, unknown>, expected: string[]) {
  const actual = Object.keys(value).sort();
  const sorted = [...expected].sort();
  return actual.length === sorted.length && actual.every((key, index) => key === sorted[index]);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
