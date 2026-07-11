import { NextRequest, NextResponse } from "next/server";

import { archiveDomainLessonAsset } from "@/lib/internal-api-client.server";
import { mappedError } from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import { assetActions, assetArchiveIds, assetInvalidInput, parseArchive, prepareAssetMutation, type AssetArchiveRouteContext } from "../../_route-shared";

export async function POST(request: NextRequest, context: AssetArchiveRouteContext) {
  const ids = await assetArchiveIds(context);
  if (!ids) return NextResponse.json({ error: "asset_not_found" }, { status: 404, headers: { "cache-control": "no-store" } });
  const prepared = await prepareAssetMutation(request, assetActions.archive, ["_csrf", "idempotency_key", "expected_course_version"]);
  if (!prepared.ok) return prepared.response;
  const parsed = parseArchive(prepared.fields);
  if (!parsed) return assetInvalidInput("form");
  try {
    const payload = await archiveDomainLessonAsset(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      { ...ids, ...parsed }
    );
    return NextResponse.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}
