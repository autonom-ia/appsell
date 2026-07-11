import { NextRequest, NextResponse } from "next/server";

import { readDomainAssetUploadStatus } from "@/lib/internal-api-client.server";
import { mappedError } from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import { assetSession, assetUploadIds, type AssetUploadRouteContext } from "../../_route-shared";

export async function GET(request: NextRequest, context: AssetUploadRouteContext) {
  const ids = await assetUploadIds(context);
  if (!ids) return NextResponse.json({ error: "asset_upload_not_found" }, { status: 404, headers: { "cache-control": "no-store" } });
  const session = await assetSession(request);
  if (!session.ok) return session.response;
  try {
    const payload = await readDomainAssetUploadStatus(
      session.authenticated.sessionId,
      session.authenticated.session,
      ids
    );
    return NextResponse.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}
