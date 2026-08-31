import { NextRequest, NextResponse } from "next/server";
import { validAssetMediaFile } from "@/lib/asset-media";

import { sessionCookieName } from "@/lib/cookies";
import {
  ARCHIVE_ASSET_CSRF_ACTION,
  CANCEL_ASSET_UPLOAD_CSRF_ACTION,
  SIGN_ASSET_UPLOAD_PART_CSRF_ACTION,
  CONFIRM_ASSET_UPLOAD_CSRF_ACTION,
  CREATE_ASSET_UPLOAD_CSRF_ACTION,
  UPDATE_ASSET_POLICY_CSRF_ACTION,
  csrfTokenForSession,
  verifyMutation
} from "@/lib/mutation-protection.server";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";
import {
  authenticatedSession,
  canonicalUuid,
  forbiddenMutation,
  mappedError,
  payloadTooLarge,
  readExactLimitedFields,
  safeVersion,
  unauthorized
} from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";

const CONTROL = /[\p{Cc}\p{Cs}]/u;
const SHA256 = /^[0-9a-f]{64}$/;
const MAX_FORM_BYTES = 16_384;

export interface LessonAssetRouteContext {
  params: Promise<{
    sellerId: string;
    productId: string;
    courseId: string;
    moduleId: string;
    lessonId: string;
  }>;
}

export interface AssetUploadRouteContext extends LessonAssetRouteContext {
  params: Promise<Awaited<LessonAssetRouteContext["params"]> & { uploadId: string }>;
}

export interface AssetArchiveRouteContext extends LessonAssetRouteContext {
  params: Promise<Awaited<LessonAssetRouteContext["params"]> & { assetId: string }>;
}

export async function lessonAssetIds(context: LessonAssetRouteContext) {
  const values = await context.params;
  const sellerOrganizationId = canonicalUuid(values.sellerId);
  const productId = canonicalUuid(values.productId);
  const courseId = canonicalUuid(values.courseId);
  const moduleId = canonicalUuid(values.moduleId);
  const lessonId = canonicalUuid(values.lessonId);
  return sellerOrganizationId && productId && courseId && moduleId && lessonId
    ? { sellerOrganizationId, productId, courseId, moduleId, lessonId }
    : null;
}

export async function assetUploadIds(context: AssetUploadRouteContext) {
  const values = await context.params;
  const base = await lessonAssetIds({ params: Promise.resolve(values) });
  const uploadId = canonicalUuid(values.uploadId);
  return base && uploadId ? { ...base, uploadId } : null;
}

export async function assetArchiveIds(context: AssetArchiveRouteContext) {
  const values = await context.params;
  const base = await lessonAssetIds({ params: Promise.resolve(values) });
  const assetId = canonicalUuid(values.assetId);
  return base && assetId ? { ...base, assetId } : null;
}

export async function assetSession(request: NextRequest) {
  try {
    const authenticated = await authenticatedSession(request);
    return authenticated
      ? { ok: true as const, authenticated }
      : { ok: false as const, response: unauthorized() };
  } catch (error) {
    return { ok: false as const, response: mappedError(error) };
  }
}

export async function prepareAssetMutation(
  request: NextRequest,
  action: string,
  fields: readonly string[] | readonly (readonly string[])[]
) {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId || !hasConfiguredOrigin(request, configuredPublicOrigin())) {
    return { ok: false as const, response: forbiddenMutation() };
  }
  const session = await assetSession(request);
  if (!session.ok) return session;
  const parsed = await readExactLimitedFields(request, fields, MAX_FORM_BYTES);
  if (parsed.kind === "too_large") return { ok: false as const, response: payloadTooLarge() };
  if (parsed.kind === "invalid") return { ok: false as const, response: assetInvalidInput("form") };
  if (!verifyMutation(request, sessionId, action, parsed.fields._csrf)) {
    return { ok: false as const, response: forbiddenMutation() };
  }
  return { ok: true as const, authenticated: session.authenticated, fields: parsed.fields };
}

export function assetCsrf(sessionId: string) {
  return {
    intent: csrfTokenForSession(sessionId, CREATE_ASSET_UPLOAD_CSRF_ACTION),
    confirm: csrfTokenForSession(sessionId, CONFIRM_ASSET_UPLOAD_CSRF_ACTION),
    cancel: csrfTokenForSession(sessionId, CANCEL_ASSET_UPLOAD_CSRF_ACTION),
    signPart: csrfTokenForSession(sessionId, SIGN_ASSET_UPLOAD_PART_CSRF_ACTION),
    archive: csrfTokenForSession(sessionId, ARCHIVE_ASSET_CSRF_ACTION),
    policy: csrfTokenForSession(sessionId, UPDATE_ASSET_POLICY_CSRF_ACTION)
  };
}

export function parseUploadIntent(fields: Record<string, string>) {
  const idempotencyKey = canonicalUuid(fields.idempotency_key);
  const existingAssetId = fields.existing_asset_id === undefined
    ? undefined
    : canonicalUuid(fields.existing_asset_id) ?? null;
  const expectedCourseVersion = safeVersion(fields.expected_course_version);
  const byteSize = safeVersion(fields.byte_size);
  const title = fields.title;
  const fileName = fields.file_name;
  const mediaType = fields.media_type;
  if (!idempotencyKey || existingAssetId === null || expectedCourseVersion === null || byteSize === null
    || !validText(title, 2, 160) || !validFile(fileName) || !validAssetMediaFile(fileName, mediaType, byteSize)
    || !SHA256.test(fields.sha256 ?? "")) return null;
  return {
    idempotencyKey,
    existingAssetId,
    expectedCourseVersion,
    title,
    fileName,
    mediaType,
    byteSize,
    sha256: fields.sha256
  };
}

export function parseMutationKey(fields: Record<string, string>) {
  return canonicalUuid(fields.idempotency_key);
}

export function parseArchive(fields: Record<string, string>) {
  const idempotencyKey = canonicalUuid(fields.idempotency_key);
  const expectedCourseVersion = safeVersion(fields.expected_course_version);
  return idempotencyKey && expectedCourseVersion !== null
    ? { idempotencyKey, expectedCourseVersion }
    : null;
}

export function parseAssetPolicy(fields: Record<string, string>) {
  const base = parseArchive(fields);
  const contentRole = fields.content_role;
  const downloadAllowed = fields.download_allowed === "true"
    ? true
    : fields.download_allowed === "false"
      ? false
      : null;
  if (!base || !["course_content", "support_material"].includes(contentRole)
    || downloadAllowed === null || (downloadAllowed && contentRole !== "support_material")) {
    return null;
  }
  return {
    ...base,
    contentRole: contentRole as "course_content" | "support_material",
    downloadAllowed
  };
}

export function assetInvalidInput(field: string) {
  return NextResponse.json({ error: "invalid_asset_input", field }, {
    status: 422,
    headers: { "cache-control": "no-store" }
  });
}

export const assetActions = {
  intent: CREATE_ASSET_UPLOAD_CSRF_ACTION,
  confirm: CONFIRM_ASSET_UPLOAD_CSRF_ACTION,
  cancel: CANCEL_ASSET_UPLOAD_CSRF_ACTION,
  signPart: SIGN_ASSET_UPLOAD_PART_CSRF_ACTION,
  archive: ARCHIVE_ASSET_CSRF_ACTION,
  policy: UPDATE_ASSET_POLICY_CSRF_ACTION
} as const;

function validText(value: string | undefined, minimum: number, maximum: number): value is string {
  if (value === undefined || value !== value.trim() || CONTROL.test(value)) return false;
  const bytes = Buffer.byteLength(value);
  return bytes >= minimum && bytes <= maximum;
}

function validFile(value: string | undefined): value is string {
  return validText(value, 1, 160) && !/[\\/]/.test(value);
}
