import { NextRequest, NextResponse } from "next/server";

import { cancelDomainAssetUpload } from "@/lib/internal-api-client.server";
import { mappedError } from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import { assetActions, assetInvalidInput, assetUploadIds, parseMutationKey, prepareAssetMutation, type AssetUploadRouteContext } from "../../../_route-shared";

export async function POST(request: NextRequest, context: AssetUploadRouteContext) {
  const ids = await assetUploadIds(context);
  if (!ids) return NextResponse.json({ error: "asset_upload_not_found" }, { status: 404, headers: { "cache-control": "no-store" } });
  const prepared = await prepareAssetMutation(request, assetActions.cancel, ["_csrf", "idempotency_key"]);
  if (!prepared.ok) return prepared.response;
  const idempotencyKey = parseMutationKey(prepared.fields);
  if (!idempotencyKey) return assetInvalidInput("idempotency_key");
  try {
    const payload = await cancelDomainAssetUpload(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      { ...ids, idempotencyKey }
    );
    return NextResponse.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}
