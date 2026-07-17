import { NextRequest, NextResponse } from "next/server";

import { readDomainLearningPdfManifest } from "@/lib/internal-api-client.server";
import {
  authenticatedLearningSession,
  learningAssetIds,
  learningError,
  learningNotFound,
  learningUnauthorized,
  type LearningAssetRouteContext
} from "../../../../_route-shared";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export async function GET(request: NextRequest, context: LearningAssetRouteContext) {
  try {
    const authenticated = await authenticatedLearningSession(request);
    if (!authenticated) return learningUnauthorized();
    const ids = await learningAssetIds(context);
    if (!ids) return learningNotFound();
    const payload = await readDomainLearningPdfManifest(
      authenticated.sessionId,
      authenticated.session,
      ids.entitlementId,
      ids.assetId
    );
    if (!validManifest(payload, ids.assetId)) return manifestUnavailable();
    return NextResponse.json({ asset_id: ids.assetId, page_count: payload.asset.page_count }, {
      headers: noStoreHeaders()
    });
  } catch (error) {
    return learningError(error);
  }
}

function validManifest(value: unknown, expectedAssetId: string): value is {
  asset: { id: string; page_count: number };
} {
  if (!isRecord(value) || Object.keys(value).length !== 1 || !isRecord(value.asset)) return false;
  const asset = value.asset;
  return Object.keys(asset).sort().join(",") === "id,page_count" &&
    asset.id === expectedAssetId && typeof asset.id === "string" && UUID.test(asset.id) &&
    Number.isSafeInteger(asset.page_count) && Number(asset.page_count) >= 1 &&
    Number(asset.page_count) <= 100;
}

function manifestUnavailable() {
  return NextResponse.json({ error: "pdf_manifest_invalid" }, {
    status: 503,
    headers: noStoreHeaders()
  });
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
