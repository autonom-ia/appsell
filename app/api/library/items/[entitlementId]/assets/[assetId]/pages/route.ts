import { NextRequest, NextResponse } from "next/server";

import { readDomainLearningPdfManifest } from "@/lib/internal-api-client.server";
import {
  parseDomainPdfManifest,
  publicPdfManifest
} from "@/lib/protected-pdf-links.server";
import {
  authenticatedLearningSession,
  learningAssetIds,
  learningError,
  learningNotFound,
  learningUnauthorized,
  type LearningAssetRouteContext
} from "../../../../_route-shared";

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
    const manifest = parseDomainPdfManifest(payload, ids.assetId);
    if (!manifest) return manifestUnavailable();
    return NextResponse.json(publicPdfManifest(manifest, request.nextUrl.pathname), {
      headers: noStoreHeaders()
    });
  } catch (error) {
    return learningError(error);
  }
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
