import { NextRequest, NextResponse } from "next/server";

import { createDomainAssetUploadIntent } from "@/lib/internal-api-client.server";
import { configuredPublicOrigin } from "@/lib/public-origin";
import {
  StorageUploadConfigurationError,
  browserMultipartUploadIntent,
  browserUploadIntent,
  configuredStorageUploadOrigin
} from "@/lib/storage-upload-policy.server";
import { isRecord, mappedError } from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import {
  assetActions,
  assetInvalidInput,
  lessonAssetIds,
  parseUploadIntent,
  prepareAssetMutation,
  type LessonAssetRouteContext
} from "../_route-shared";

const REQUIRED = ["_csrf", "idempotency_key", "expected_course_version", "title", "file_name", "media_type", "byte_size", "sha256"] as const;
const WITH_EXISTING = [...REQUIRED, "existing_asset_id"] as const;

export async function POST(request: NextRequest, context: LessonAssetRouteContext) {
  const ids = await lessonAssetIds(context);
  if (!ids) return NextResponse.json({ error: "asset_not_found" }, { status: 404, headers: { "cache-control": "no-store" } });
  const prepared = await prepareAssetMutation(request, assetActions.intent, [REQUIRED, WITH_EXISTING]);
  if (!prepared.ok) return prepared.response;
  const parsed = parseUploadIntent(prepared.fields);
  if (!parsed) return assetInvalidInput("form");
  try {
    const appOrigin = configuredPublicOrigin();
    const storageOrigin = configuredStorageUploadOrigin();
    const payload = await createDomainAssetUploadIntent(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      { ...ids, ...parsed }
    );
    if (!isRecord(payload)) return NextResponse.json({ error: "invalid_asset_response" }, { status: 502, headers: { "cache-control": "no-store" } });
    // Um envio fatiado não traz URL, e por isso passa por outro saneador. Deixar
    // o mesmo aceitar os dois formatos faria uma resposta sem URL atravessar a
    // prova de endereço sem ninguém notar.
    const safeIntent = payload.multipart === undefined
      ? browserUploadIntent(payload, appOrigin, storageOrigin)
      : browserMultipartUploadIntent(payload);
    if (!safeIntent) return NextResponse.json({ error: "invalid_asset_response" }, { status: 502, headers: { "cache-control": "no-store" } });
    return NextResponse.json(safeIntent, { status: payload.created === false ? 200 : 201, headers: { "cache-control": "no-store" } });
  } catch (error) {
    if (error instanceof StorageUploadConfigurationError) {
      return NextResponse.json({ error: "storage_upload_configuration" }, { status: 503, headers: { "cache-control": "no-store" } });
    }
    return mappedError(error);
  }
}
