import { NextRequest, NextResponse } from "next/server";

import { clearDomainSellerLogo } from "@/lib/internal-api-client.server";
import {
  canonicalUuid,
  invalidInput,
  mappedError,
  notFound
} from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import {
  imageIds,
  prepareSettingsMutation,
  settingsActions,
  type ImageRouteContext
} from "../../_route-shared";

const REQUIRED = ["_csrf", "idempotency_key"] as const;

export async function POST(request: NextRequest, context: ImageRouteContext) {
  const ids = await imageIds(context);
  if (!ids) return notFound();
  const { sellerId, kind } = ids;
  const prepared = await prepareSettingsMutation(request, settingsActions.logoClear, REQUIRED);
  if (!prepared.ok) return prepared.response;
  const idempotencyKey = canonicalUuid(prepared.fields.idempotency_key);
  if (!idempotencyKey) return invalidInput("form");
  try {
    const payload = await clearDomainSellerLogo(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      { sellerOrganizationId: sellerId, kind, idempotencyKey }
    );
    return NextResponse.json(payload, { status: 200, headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}
