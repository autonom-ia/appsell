import { NextRequest, NextResponse } from "next/server";

import { readDomainLessonAssets } from "@/lib/internal-api-client.server";
import { isRecord, mappedError } from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import { assetCsrf, assetSession, lessonAssetIds, type LessonAssetRouteContext } from "./_route-shared";

export async function GET(request: NextRequest, context: LessonAssetRouteContext) {
  const ids = await lessonAssetIds(context);
  if (!ids) return NextResponse.json({ error: "asset_not_found" }, { status: 404, headers: { "cache-control": "no-store" } });
  const session = await assetSession(request);
  if (!session.ok) return session.response;
  try {
    const payload = await readDomainLessonAssets(
      session.authenticated.sessionId,
      session.authenticated.session,
      ids
    );
    if (!isRecord(payload)) return NextResponse.json({ error: "invalid_asset_response" }, { status: 502, headers: { "cache-control": "no-store" } });
    return NextResponse.json({ ...payload, csrf: assetCsrf(session.authenticated.sessionId) }, {
      headers: { "cache-control": "no-store" }
    });
  } catch (error) {
    return mappedError(error);
  }
}
