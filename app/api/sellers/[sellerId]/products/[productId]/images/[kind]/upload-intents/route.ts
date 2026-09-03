import { NextRequest, NextResponse } from "next/server";

import { createDomainCoverUploadIntent } from "@/lib/internal-api-client.server";
import { configuredPublicOrigin } from "@/lib/public-origin";
import {
  StorageUploadConfigurationError,
  browserCoverUploadIntent,
  configuredStorageUploadOrigin
} from "@/lib/storage-upload-policy.server";
import {
  invalidInput,
  mappedError,
  notFound
} from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import {
  coverActions,
  coverIds,
  parseCoverIntent,
  prepareCoverMutation,
  type CoverRouteContext
} from "../_route-shared";

const REQUIRED = ["_csrf", "idempotency_key", "media_type", "byte_size", "sha256"] as const;
const NO_STORE = { "cache-control": "no-store" } as const;

export async function POST(request: NextRequest, context: CoverRouteContext) {
  const ids = await coverIds(context);
  if (!ids) return notFound();
  const prepared = await prepareCoverMutation(request, coverActions.intent, REQUIRED);
  if (!prepared.ok) return prepared.response;
  const parsed = parseCoverIntent(prepared.fields);
  if (!parsed) return invalidInput("form");
  try {
    const appOrigin = configuredPublicOrigin();
    const storageOrigin = configuredStorageUploadOrigin();
    const payload = await createDomainCoverUploadIntent(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      { ...ids, ...parsed }
    );
    const safeIntent = browserCoverUploadIntent(payload, appOrigin, storageOrigin);
    if (!safeIntent) {
      return NextResponse.json({ error: "invalid_cover_response" }, { status: 502, headers: NO_STORE });
    }
    return NextResponse.json(safeIntent, { status: 201, headers: NO_STORE });
  } catch (error) {
    if (error instanceof StorageUploadConfigurationError) {
      return NextResponse.json({ error: "storage_upload_configuration" }, { status: 503, headers: NO_STORE });
    }
    return mappedError(error);
  }
}
