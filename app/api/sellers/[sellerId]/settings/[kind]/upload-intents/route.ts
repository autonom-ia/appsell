import { NextRequest, NextResponse } from "next/server";

import { createDomainLogoUploadIntent } from "@/lib/internal-api-client.server";
import { configuredPublicOrigin } from "@/lib/public-origin";
import {
  StorageUploadConfigurationError,
  browserLogoUploadIntent,
  configuredStorageUploadOrigin
} from "@/lib/storage-upload-policy.server";
import {
  canonicalUuid,
  invalidInput,
  mappedError,
  notFound,
  safeVersion
} from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import {
  imageIds,
  prepareSettingsMutation,
  settingsActions,
  type ImageRouteContext
} from "../../_route-shared";

const REQUIRED = ["_csrf", "idempotency_key", "media_type", "byte_size", "sha256"] as const;
const SHA256 = /^[0-9a-f]{64}$/;
const LOGO_MEDIA = new Set(["image/jpeg", "image/png", "image/webp"]);
const LOGO_MAX_BYTES = 5 * 1024 * 1024;
const NO_STORE = { "cache-control": "no-store" } as const;

export async function POST(request: NextRequest, context: ImageRouteContext) {
  const ids = await imageIds(context);
  if (!ids) return notFound();
  const { sellerId, kind } = ids;
  const prepared = await prepareSettingsMutation(request, settingsActions.logoIntent, REQUIRED);
  if (!prepared.ok) return prepared.response;
  const idempotencyKey = canonicalUuid(prepared.fields.idempotency_key);
  const byteSize = safeVersion(prepared.fields.byte_size);
  const mediaType = prepared.fields.media_type;
  const sha256 = prepared.fields.sha256;
  if (
    !idempotencyKey ||
    byteSize === null ||
    byteSize > LOGO_MAX_BYTES ||
    !LOGO_MEDIA.has(mediaType) ||
    !SHA256.test(sha256)
  ) return invalidInput("form");
  try {
    const appOrigin = configuredPublicOrigin();
    const storageOrigin = configuredStorageUploadOrigin();
    const payload = await createDomainLogoUploadIntent(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      { sellerOrganizationId: sellerId, kind, idempotencyKey, mediaType, byteSize, sha256 }
    );
    const safeIntent = browserLogoUploadIntent(payload, appOrigin, storageOrigin);
    if (!safeIntent) {
      return NextResponse.json({ error: "invalid_logo_response" }, { status: 502, headers: NO_STORE });
    }
    return NextResponse.json(safeIntent, { status: 201, headers: NO_STORE });
  } catch (error) {
    if (error instanceof StorageUploadConfigurationError) {
      return NextResponse.json({ error: "storage_upload_configuration" }, { status: 503, headers: NO_STORE });
    }
    return mappedError(error);
  }
}
