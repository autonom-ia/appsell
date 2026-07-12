import { Readable } from "node:stream";
import { NextRequest, NextResponse } from "next/server";

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

const FILE_NAME = /^[a-z0-9][a-z0-9-]{1,123}[.]pdf$/;
const SHA256 = /^[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const MAX_ASSET_BYTES = 10 * 1024 * 1024;

interface AssetMetadata {
  id: string;
  delivery: "legacy_bundle";
  seed_key: string;
  file_name: string;
  title: string;
  media_type: "application/pdf";
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
  media_type: "application/pdf";
  byte_size: number;
  sha256: string;
}

export async function GET(request: NextRequest, context: LearningAssetRouteContext) {
  try {
    const authenticated = await authenticatedLearningSession(request);
    if (!authenticated) return learningUnauthorized();
    const ids = await learningAssetIds(context);
    if (!ids) return learningNotFound();
    const authorization = await authorizeDomainLearningAsset(
      authenticated.sessionId,
      authenticated.session,
      ids.entitlementId,
      ids.assetId
    );
    const metadata = authorizedMetadata(authorization, ids.assetId);
    if (!metadata) return assetUnavailable("asset_metadata_invalid");
    if (metadata.delivery === "redirect") {
      const response = NextResponse.redirect(metadata.download_url, 303);
      response.headers.set("cache-control", "private, no-store, max-age=0");
      response.headers.set("referrer-policy", "no-referrer");
      response.headers.set("surrogate-control", "no-store");
      response.headers.set("vary", "Cookie");
      response.headers.set("x-content-type-options", "nosniff");
      return response;
    }
    const asset = await prepareVerifiedPrivateLearningAsset(
      metadata,
      configuredPrivateLearningAssetRoot()
    );
    if (!asset) return assetUnavailable("asset_integrity_failure");
    const nodeStream = asset.handle.createReadStream({ autoClose: true, start: 0 });
    return new NextResponse(Readable.toWeb(nodeStream) as ReadableStream<Uint8Array>, {
      headers: {
        "accept-ranges": "none",
        "cache-control": "private, no-store, max-age=0",
        "content-disposition": `inline; filename="${metadata.file_name}"`,
        "content-length": String(asset.size),
        "content-security-policy": "sandbox",
        "content-type": "application/pdf",
        "cross-origin-resource-policy": "same-origin",
        pragma: "no-cache",
        "surrogate-control": "no-store",
        vary: "Cookie",
        "x-content-type-options": "nosniff"
      }
    });
  } catch (error) {
    return learningError(error);
  }
}

function authorizedMetadata(value: unknown, expectedAssetId: string): AssetMetadata | RedirectAssetMetadata | null {
  if (!isRecord(value) || !hasExactKeys(value, ["asset"]) || !isRecord(value.asset)) {
    return null;
  }
  const asset = value.asset;
  const commonValid = asset.id === expectedAssetId && typeof asset.id === "string" && UUID.test(asset.id)
    && typeof asset.file_name === "string" && asset.file_name.length >= 1 && asset.file_name.length <= 160
    && !/[\\/\u0000-\u001f\u007f]/.test(asset.file_name)
    && typeof asset.title === "string" && asset.title.length >= 2
    && asset.media_type === "application/pdf"
    && Number.isInteger(asset.byte_size) && Number(asset.byte_size) >= 1
    && Number(asset.byte_size) <= MAX_ASSET_BYTES
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
  ]) && typeof asset.seed_key === "string" && FILE_NAME.test(`${asset.seed_key}.pdf`)) {
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

function hasExactKeys(value: Record<string, unknown>, expected: string[]) {
  const actual = Object.keys(value).sort();
  const sorted = [...expected].sort();
  return actual.length === sorted.length && actual.every((key, index) => key === sorted[index]);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
