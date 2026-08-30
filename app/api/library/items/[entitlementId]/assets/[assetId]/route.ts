import { Readable } from "node:stream";
import { NextRequest, NextResponse } from "next/server";

import { assetMediaSpec, type AssetMediaType } from "@/lib/asset-media";
import { authorizeDomainLearningAsset } from "@/lib/internal-api-client.server";
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
} from "../../../_route-shared";
import { configuredPublicOrigin } from "@/lib/public-origin";
import {
  configuredStorageUploadOrigin,
  trustedStorageDownloadUrl
} from "@/lib/storage-upload-policy.server";
import { parseSingleByteRange } from "@/lib/http-byte-range";

const SHA256 = /^[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
// O catálogo, não números escritos à mão. Estes dois estavam parados no valor
// antigo: o PDF em 10 MiB, teto que a #287 levou a 100 MiB em 26/08/2026 — um
// playbook de 12 MiB era recusado nesta rota depois de ter sido aceito em todas
// as outras.
type MediaType = AssetMediaType;

interface AssetMetadata {
  id: string;
  delivery: "legacy_bundle";
  seed_key: string;
  file_name: string;
  title: string;
  media_type: MediaType;
  byte_size: number;
  sha256: string;
}

interface RedirectAssetMetadata {
  id: string;
  delivery: "redirect";
  download_url: string;
  expires_in_seconds: number;
  file_name: string;
  title: string;
  media_type: MediaType;
  byte_size: number;
  sha256: string;
}

interface DownloadOnlyMetadata {
  id: string;
  delivery: "download_only";
  file_name: string;
  title: string;
  media_type: string;
  byte_size: number;
  sha256: string;
}

interface ProtectedPdfMetadata {
  id: string;
  delivery: "protected_pages";
  file_name: string;
  title: string;
  media_type: "application/pdf";
  byte_size: number;
  sha256: string;
}

export async function GET(request: NextRequest, context: LearningAssetRouteContext) {
  try {
    const authorized = await authorizeAssetRequest(request, context);
    if (!authorized.ok) return authorized.response;
    const metadata = authorized.metadata;
    if (metadata.media_type === "application/pdf") return pdfOriginalUnavailable();
    // A document is only ever handed over by the download route, which checks
    // that the seller marked the material downloadable. This route never carries
    // one, so it cannot become a way around that check.
    if (metadata.delivery === "download_only") return documentRequiresDownload();
    if (metadata.delivery === "redirect") {
      return redirectAsset(metadata);
    }
    const range = metadata.media_type === "video/mp4"
      ? parseSingleByteRange(request.headers.get("range"), metadata.byte_size)
      : null;
    if (range === "unsatisfiable") {
      return new NextResponse(null, {
        status: 416,
        headers: secureAssetHeaders(metadata, 0, {
          "content-range": `bytes */${metadata.byte_size}`
        })
      });
    }
    const asset = await prepareVerifiedPrivateLearningAsset(
      metadata,
      configuredPrivateLearningAssetRoot()
    );
    if (!asset) return assetUnavailable("asset_integrity_failure");
    const start = range?.start ?? 0;
    const end = range?.end ?? asset.size - 1;
    const nodeStream = asset.handle.createReadStream({ autoClose: true, start, end });
    return new NextResponse(Readable.toWeb(nodeStream) as ReadableStream<Uint8Array>, {
      status: range ? 206 : 200,
      headers: secureAssetHeaders(metadata, end - start + 1, range ? {
        "content-range": `bytes ${start}-${end}/${asset.size}`
      } : undefined)
    });
  } catch (error) {
    return learningError(error);
  }
}

export async function HEAD(request: NextRequest, context: LearningAssetRouteContext) {
  try {
    const authorized = await authorizeAssetRequest(request, context);
    if (!authorized.ok) return authorized.response;
    const metadata = authorized.metadata;
    if (metadata.media_type === "application/pdf") return pdfOriginalUnavailable();
    // HEAD must answer exactly what GET would, or a probe learns more than a read.
    if (metadata.delivery === "download_only") return documentRequiresDownload();
    return new NextResponse(null, {
      status: 200,
      headers: secureAssetHeaders(metadata, metadata.byte_size)
    });
  } catch (error) {
    return learningError(error);
  }
}

async function authorizeAssetRequest(
  request: NextRequest,
  context: LearningAssetRouteContext
): Promise<
  | { ok: true; metadata: AssetMetadata | RedirectAssetMetadata | ProtectedPdfMetadata | DownloadOnlyMetadata }
  | { ok: false; response: NextResponse }
> {
  const authenticated = await authenticatedLearningSession(request);
  if (!authenticated) return { ok: false, response: learningUnauthorized() };
  const ids = await learningAssetIds(context);
  if (!ids) return { ok: false, response: learningNotFound() };
  const authorization = await authorizeDomainLearningAsset(
    authenticated.sessionId,
    authenticated.session,
    ids.entitlementId,
    ids.assetId
  );
  const metadata = authorizedMetadata(authorization, ids.assetId);
  return metadata
    ? { ok: true, metadata }
    : { ok: false, response: assetUnavailable("asset_metadata_invalid") };
}

function redirectAsset(metadata: RedirectAssetMetadata) {
  const response = NextResponse.redirect(metadata.download_url, 303);
  response.headers.set("cache-control", "private, no-store, max-age=0");
  response.headers.set("referrer-policy", "no-referrer");
  response.headers.set("surrogate-control", "no-store");
  response.headers.set("vary", "Cookie");
  response.headers.set("x-content-type-options", "nosniff");
  return response;
}

function authorizedMetadata(value: unknown, expectedAssetId: string): AssetMetadata | RedirectAssetMetadata | ProtectedPdfMetadata | DownloadOnlyMetadata | null {
  if (!isRecord(value) || !hasExactKeys(value, ["asset"]) || !isRecord(value.asset)) {
    return null;
  }
  const asset = value.asset;
  const commonValid = asset.id === expectedAssetId && typeof asset.id === "string" && UUID.test(asset.id)
    && typeof asset.file_name === "string" && asset.file_name.length >= 1 && asset.file_name.length <= 160
    && !/[\\/\u0000-\u001f\u007f]/.test(asset.file_name)
    && typeof asset.title === "string" && asset.title.length >= 2
    && validAssetFile(asset.file_name, asset.media_type)
    && Number.isInteger(asset.byte_size) && Number(asset.byte_size) >= 1
    && Number(asset.byte_size) <= maxAssetBytes(asset.media_type)
    && typeof asset.sha256 === "string" && SHA256.test(asset.sha256);
  if (!commonValid) return null;
  if (asset.delivery === "legacy_bundle" && hasExactKeys(asset, [
    "byte_size",
    "delivery",
    "file_name",
    "id",
    "media_type",
    "seed_key",
    "sha256",
    "title"
  ]) && asset.seed_key === seedKey(asset.file_name, asset.media_type)) {
    return asset as unknown as AssetMetadata;
  }
  if (asset.delivery === "redirect" && hasExactKeys(asset, [
    "byte_size",
    "delivery",
    "download_url",
    "expires_in_seconds",
    "file_name",
    "id",
    "media_type",
    "sha256",
    "title"
  ]) && validDownloadUrl(asset.download_url, asset.expires_in_seconds)) {
    return asset as unknown as RedirectAssetMetadata;
  }
  if (asset.delivery === "download_only" && hasExactKeys(asset, [
    "byte_size",
    "delivery",
    "file_name",
    "id",
    "media_type",
    "sha256",
    "title"
  ])) {
    return asset as unknown as DownloadOnlyMetadata;
  }
  if (asset.delivery === "protected_pages" && asset.media_type === "application/pdf" && hasExactKeys(asset, [
    "byte_size",
    "delivery",
    "file_name",
    "id",
    "media_type",
    "sha256",
    "title"
  ])) {
    return asset as unknown as ProtectedPdfMetadata;
  }
  return null;
}

function documentRequiresDownload() {
  return NextResponse.json(
    { error: "document_requires_download_permission" },
    { status: 404, headers: { "cache-control": "private, no-store, max-age=0" } }
  );
}

function secureAssetHeaders(metadata: AssetMetadata | RedirectAssetMetadata | ProtectedPdfMetadata, contentLength: number, additional?: Record<string, string>) {
  return {
    "accept-ranges": metadata.media_type === "video/mp4" ? "bytes" : "none",
    "cache-control": "private, no-store, max-age=0",
    "content-disposition": inlineContentDisposition(metadata.file_name),
    "content-length": String(contentLength),
    "content-security-policy": "sandbox",
    "content-type": metadata.media_type,
    "cross-origin-resource-policy": "same-origin",
    pragma: "no-cache",
    "referrer-policy": "no-referrer",
    "surrogate-control": "no-store",
    vary: "Cookie",
    "x-content-type-options": "nosniff",
    ...additional
  };
}

function validAssetFile(fileName: unknown, mediaType: unknown): mediaType is MediaType {
  if (typeof fileName !== "string" || Buffer.byteLength(fileName) > 160
    || fileName !== fileName.trim() || /[\\/\u0000-\u001f\u007f]/.test(fileName)) return false;
  const spec = assetMediaSpec(mediaType);
  return spec !== null && fileName.toLowerCase().endsWith(`.${spec.extension}`);
}

function inlineContentDisposition(fileName: string) {
  const asciiFallback = fileName.normalize("NFKD")
    .replace(/[^\x20-\x7e]/g, "_")
    .replace(/["\\]/g, "_");
  const encoded = encodeURIComponent(fileName).replace(/[!'()*]/g, (character) =>
    `%${character.charCodeAt(0).toString(16).toUpperCase()}`);
  return `inline; filename="${asciiFallback}"; filename*=UTF-8''${encoded}`;
}

function maxAssetBytes(mediaType: unknown) {
  return assetMediaSpec(mediaType)?.maxBytes ?? 0;
}

function seedKey(fileName: unknown, mediaType: unknown) {
  if (typeof fileName !== "string") return null;
  if (mediaType === "application/pdf") return fileName.slice(0, -4);
  if (mediaType === "video/mp4") return fileName.slice(0, -4);
  return null;
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

function assetUnavailable(error: string) {
  return NextResponse.json({ error }, {
    status: 503,
    headers: {
      "cache-control": "private, no-store, max-age=0",
      pragma: "no-cache",
      "surrogate-control": "no-store",
      vary: "Cookie",
      "x-content-type-options": "nosniff"
    }
  });
}

function pdfOriginalUnavailable() {
  return NextResponse.json({ error: "pdf_original_requires_download_permission" }, {
    status: 404,
    headers: {
      "cache-control": "private, no-store, max-age=0",
      pragma: "no-cache",
      "surrogate-control": "no-store",
      vary: "Cookie",
      "x-content-type-options": "nosniff"
    }
  });
}

function hasExactKeys(value: Record<string, unknown>, expected: string[]) {
  const actual = Object.keys(value).sort();
  const sorted = [...expected].sort();
  return actual.length === sorted.length && actual.every((key, index) => key === sorted[index]);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
