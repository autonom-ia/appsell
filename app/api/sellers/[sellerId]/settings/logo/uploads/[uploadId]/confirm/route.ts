import { NextRequest, NextResponse } from "next/server";

import { confirmDomainLogoUpload } from "@/lib/internal-api-client.server";
import {
  canonicalUuid,
  invalidInput,
  mappedError,
  notFound
} from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import {
  logoUploadIds,
  prepareSettingsMutation,
  settingsActions,
  type LogoUploadRouteContext
} from "../../../../_route-shared";

const REQUIRED = ["_csrf", "idempotency_key"] as const;

export async function POST(request: NextRequest, context: LogoUploadRouteContext) {
  const ids = await logoUploadIds(context);
  if (!ids) return notFound();
  const prepared = await prepareSettingsMutation(request, settingsActions.logoConfirm, REQUIRED);
  if (!prepared.ok) return prepared.response;
  const idempotencyKey = canonicalUuid(prepared.fields.idempotency_key);
  if (!idempotencyKey) return invalidInput("form");
  try {
    const payload = await confirmDomainLogoUpload(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      { sellerOrganizationId: ids.sellerId, uploadId: ids.uploadId, idempotencyKey }
    );
    return NextResponse.json(payload, { status: 200, headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}
