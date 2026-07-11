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

const FILE_NAME = /^[a-z0-9][a-z0-9-]{1,123}[.]pdf$/;
const SHA256 = /^[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const MAX_ASSET_BYTES = 10 * 1024 * 1024;

interface AssetMetadata {
  id: string;
  seed_key: string;
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

function authorizedMetadata(value: unknown, expectedAssetId: string): AssetMetadata | null {
  if (!isRecord(value) || !hasExactKeys(value, ["asset"]) || !isRecord(value.asset)) {
    return null;
  }
  const asset = value.asset;
  if (!hasExactKeys(asset, [
    "byte_size",
    "file_name",
    "id",
    "media_type",
    "seed_key",
    "sha256",
    "title"
  ])) return null;
  if (
    asset.id !== expectedAssetId || typeof asset.id !== "string" || !UUID.test(asset.id) ||
    typeof asset.seed_key !== "string" ||
    typeof asset.file_name !== "string" || !FILE_NAME.test(asset.file_name) ||
    typeof asset.title !== "string" || asset.title.length < 2 ||
    asset.media_type !== "application/pdf" ||
    !Number.isInteger(asset.byte_size) || Number(asset.byte_size) < 1 ||
    Number(asset.byte_size) > MAX_ASSET_BYTES ||
    typeof asset.sha256 !== "string" || !SHA256.test(asset.sha256)
  ) return null;
  return asset as unknown as AssetMetadata;
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
