import { NextRequest, NextResponse } from "next/server";

import { updateDomainLessonAssetPolicy } from "@/lib/internal-api-client.server";
import { mappedError } from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import {
  assetActions,
  assetArchiveIds,
  assetInvalidInput,
  parseAssetPolicy,
  prepareAssetMutation,
  type AssetArchiveRouteContext
} from "../../_route-shared";

const POLICY_FIELDS = [
  "_csrf",
  "idempotency_key",
  "expected_course_version",
  "content_role",
  "download_allowed"
] as const;

export async function PATCH(request: NextRequest, context: AssetArchiveRouteContext) {
  const ids = await assetArchiveIds(context);
  if (!ids) {
    return NextResponse.json(
      { error: "asset_not_found" },
      { status: 404, headers: { "cache-control": "no-store" } }
    );
  }
  const prepared = await prepareAssetMutation(request, assetActions.policy, POLICY_FIELDS);
  if (!prepared.ok) return prepared.response;
  const parsed = parseAssetPolicy(prepared.fields);
  if (!parsed) return assetInvalidInput("policy");
  try {
    const payload = await updateDomainLessonAssetPolicy(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      { ...ids, ...parsed }
    );
    return NextResponse.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}
