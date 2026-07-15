import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import { DomainApiError } from "@/lib/internal-api-client.server";
import { PayloadTooLargeError, readLimitedForm } from "@/lib/limited-form.server";
import {
  COMPLETE_LEARNING_LESSON_CSRF_ACTION,
  UPDATE_LEARNING_PROGRESS_CSRF_ACTION,
  csrfTokenForSession,
  verifyMutation
} from "@/lib/mutation-protection.server";
import { oauthService } from "@/lib/oauth-service.server";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const MAX_FORM_BYTES = 4_096;
const NO_STORE_HEADERS = {
  "cache-control": "private, no-store, max-age=0",
  pragma: "no-cache",
  "surrogate-control": "no-store",
  vary: "Cookie",
  "x-content-type-options": "nosniff"
} as const;

type DomainSelectedAsset = {
  id: string;
  title: string;
  media_type: "application/pdf" | "video/mp4";
};

interface LearningPlayerEnvelope {
  player: Record<string, unknown> & {
    selected_asset: DomainSelectedAsset | null;
    selected_assets?: DomainSelectedAsset[];
  };
}

export interface LearningItemRouteContext {
  params: Promise<{ entitlementId: string }>;
}

export interface LearningLessonRouteContext {
  params: Promise<{ entitlementId: string; lessonId: string }>;
}

export interface LearningAssetRouteContext {
  params: Promise<{ entitlementId: string; assetId: string }>;
}

export async function learningItemId(context: LearningItemRouteContext) {
  const { entitlementId } = await context.params;
  return canonicalUuid(entitlementId);
}

export async function learningLessonIds(context: LearningLessonRouteContext) {
  const { entitlementId, lessonId } = await context.params;
  const canonicalEntitlement = canonicalUuid(entitlementId);
  const canonicalLesson = canonicalUuid(lessonId);
  return canonicalEntitlement && canonicalLesson
    ? { entitlementId: canonicalEntitlement, lessonId: canonicalLesson }
    : null;
}

export async function learningAssetIds(context: LearningAssetRouteContext) {
  const { entitlementId, assetId } = await context.params;
  const canonicalEntitlement = canonicalUuid(entitlementId);
  const canonicalAsset = canonicalUuid(assetId);
  return canonicalEntitlement && canonicalAsset
    ? { entitlementId: canonicalEntitlement, assetId: canonicalAsset }
    : null;
}

export async function authenticatedLearningSession(request: NextRequest) {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId) return null;
  const session = await oauthService().getSession(sessionId);
  return session ? { sessionId, session } : null;
}

export function learningCsrf(sessionId: string) {
  return {
    progress: csrfTokenForSession(sessionId, UPDATE_LEARNING_PROGRESS_CSRF_ACTION),
    complete: csrfTokenForSession(sessionId, COMPLETE_LEARNING_LESSON_CSRF_ACTION)
  };
}

export async function prepareLearningMutation(
  request: NextRequest,
  action: string,
  expectedFields: readonly string[]
) {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId) {
    return { ok: false as const, response: learningUnauthorized() };
  }

  let authenticated: Awaited<ReturnType<typeof authenticatedLearningSession>>;
  try {
    authenticated = await authenticatedLearningSession(request);
  } catch (error) {
    return { ok: false as const, response: learningError(error) };
  }
  if (!authenticated) {
    return { ok: false as const, response: learningUnauthorized() };
  }

  let form: FormData | null;
  try {
    form = await readLimitedForm(request, MAX_FORM_BYTES);
  } catch (error) {
    return {
      ok: false as const,
      response: error instanceof PayloadTooLargeError
        ? learningJsonError("payload_too_large", 413)
        : learningJsonError("invalid_request_body", 422)
    };
  }
  if (!form) {
    return { ok: false as const, response: learningJsonError("invalid_request_body", 422) };
  }

  const allowed = new Set(expectedFields);
  const fields: Record<string, string> = {};
  for (const [name, value] of form.entries()) {
    if (!allowed.has(name) || Object.hasOwn(fields, name) || typeof value !== "string") {
      return { ok: false as const, response: learningJsonError("invalid_request_body", 422) };
    }
    fields[name] = value;
  }
  if (
    Object.keys(fields).length !== expectedFields.length ||
    !expectedFields.every((name) => Object.hasOwn(fields, name))
  ) {
    return { ok: false as const, response: learningJsonError("invalid_request_body", 422) };
  }
  if (!verifyMutation(request, sessionId, action, fields._csrf)) {
    return { ok: false as const, response: learningJsonError("forbidden_mutation", 403) };
  }
  return { ok: true as const, authenticated, fields };
}

export function validIdempotencyKey(value: string | undefined) {
  return canonicalUuid(value);
}

export function validProgressPercent(value: string | undefined) {
  return safeInteger(value, 0, 100);
}

export function validExpectedVersion(value: string | undefined) {
  return safeInteger(value, 0, Number.MAX_SAFE_INTEGER);
}

export function learningPlayerResponse(payload: unknown, sessionId: string) {
  if (!isLearningPlayerEnvelope(payload)) {
    return learningJsonError("invalid_learning_response", 503);
  }
  const domainAssets = payload.player.selected_assets ??
    (payload.player.selected_asset === null ? [] : [payload.player.selected_asset]);
  const selectedAssets = domainAssets.map((asset) => ({
    href: `/api/library/items/${encodeURIComponent(String(payload.player.entitlement_id))}` +
      `/assets/${encodeURIComponent(asset.id)}`,
    title: asset.title,
    media_type: asset.media_type
  }));
  const player = {
    ...payload.player,
    selected_asset: selectedAssets[0] ?? null,
    selected_assets: selectedAssets
  };
  return NextResponse.json({ player, csrf: learningCsrf(sessionId) }, {
    headers: NO_STORE_HEADERS
  });
}

export function learningProgressResponse(payload: unknown) {
  if (!isLearningProgressEnvelope(payload)) {
    return learningJsonError("invalid_learning_response", 503);
  }
  return NextResponse.json(payload, { headers: NO_STORE_HEADERS });
}

export function learningUnauthorized() {
  return learningJsonError("unauthorized", 401);
}

export function learningNotFound() {
  return learningJsonError("learning_item_unavailable", 404);
}

export function learningJsonErrorForInvalidInput() {
  return learningJsonError("invalid_request_body", 422);
}

export function learningError(error: unknown) {
  if (isOAuthStoreUnavailable(error)) {
    return learningJsonError("oauth_store_unavailable", 503);
  }
  if (error instanceof DomainApiError) {
    if (error.status === 403 || error.status === 404) return learningNotFound();
    if (error.status === 409 && [
      "idempotency_conflict",
      "progress_version_conflict"
    ].includes(error.code)) {
      return learningJsonError(error.code, 409);
    }
    if (error.status === 413) return learningJsonError("payload_too_large", 413);
    if (error.status === 422) return learningJsonError("invalid_request_body", 422);
    if (error.status === 429) return learningJsonError("rate_limited", 429);
  }
  return learningJsonError("learning_unavailable", 503);
}

export const learningActions = {
  progress: UPDATE_LEARNING_PROGRESS_CSRF_ACTION,
  complete: COMPLETE_LEARNING_LESSON_CSRF_ACTION
} as const;

function learningJsonError(error: string, status: number) {
  return NextResponse.json({ error }, { status, headers: NO_STORE_HEADERS });
}

function canonicalUuid(value: string | undefined) {
  return value !== undefined && UUID.test(value) ? value : null;
}

function safeInteger(value: string | undefined, minimum: number, maximum: number) {
  if (value === undefined || !/^(?:0|[1-9][0-9]*)$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= minimum && parsed <= maximum
    ? parsed
    : null;
}

function isLearningPlayerEnvelope(value: unknown): value is LearningPlayerEnvelope {
  if (!hasExactKeys(value, ["player"]) || !isRecord(value.player)) return false;
  const player = value.player;
  const playerKeys = [
    "course",
    "course_progress_percent",
    "enrollment_id",
    "entitlement_id",
    "lesson_progress",
    "product",
    "publication",
    "resume_lesson_id",
    "selected_asset",
    "selected_lesson_id",
    "seller",
    "state_version"
  ];
  const hasMultiAssetContract = Object.hasOwn(player, "selected_assets");
  if (!hasExactKeys(player, hasMultiAssetContract
    ? [...playerKeys, "selected_assets"]
    : playerKeys)) return false;
  if (!isSelectedAsset(player.selected_asset)) return false;
  if (hasMultiAssetContract && !isSelectedAssets(player.selected_assets, player.selected_asset)) {
    return false;
  }
  return isUuidRecord(player.seller, ["id"]) &&
    isProduct(player.product) && isPublication(player.publication) &&
    UUID.test(String(player.entitlement_id)) && UUID.test(String(player.enrollment_id)) &&
    isCourse(player.course) && nullableUuid(player.selected_lesson_id) &&
    nullableUuid(player.resume_lesson_id) &&
    isPercent(player.course_progress_percent) &&
    isVersion(player.state_version) && Array.isArray(player.lesson_progress) &&
    player.lesson_progress.every(isLessonProgress);
}

function isLearningProgressEnvelope(value: unknown) {
  if (!hasExactKeys(value, ["progress"]) || !isRecord(value.progress)) return false;
  const progress = value.progress;
  return hasExactKeys(progress, [
    "applied",
    "course_progress_percent",
    "enrollment_id",
    "entitlement_id",
    "lesson_id",
    "lesson_progress_percent",
    "resume_lesson_id",
    "state_version"
  ]) && UUID.test(String(progress.entitlement_id)) &&
    UUID.test(String(progress.enrollment_id)) && UUID.test(String(progress.lesson_id)) &&
    isPercent(progress.lesson_progress_percent) &&
    isPercent(progress.course_progress_percent) && nullableUuid(progress.resume_lesson_id) &&
    isVersion(progress.state_version) && typeof progress.applied === "boolean";
}

function isProduct(value: unknown) {
  return hasExactKeys(value, ["description", "id", "slug", "title"]) &&
    UUID.test(String(value.id)) && [value.slug, value.title, value.description]
      .every((entry) => typeof entry === "string");
}

function isPublication(value: unknown) {
  return hasExactKeys(value, ["id", "source_sha256", "version"]) &&
    UUID.test(String(value.id)) && isVersion(value.version) &&
    typeof value.source_sha256 === "string" && SHA256.test(value.source_sha256);
}

function isSelectedAsset(value: unknown): value is DomainSelectedAsset | null {
  return value === null || (
    hasExactKeys(value, ["id", "media_type", "title"]) &&
    typeof value.id === "string" && UUID.test(value.id) &&
    typeof value.title === "string" && value.title.length > 0 &&
    (value.media_type === "application/pdf" || value.media_type === "video/mp4")
  );
}

function isSelectedAssets(
  value: unknown,
  legacySelection: DomainSelectedAsset | null
): value is DomainSelectedAsset[] {
  if (!Array.isArray(value) || value.length > 100 || !value.every(isSelectedAssetRecord)) {
    return false;
  }
  if (new Set(value.map((asset) => asset.id)).size !== value.length) return false;
  const first = value[0] ?? null;
  return first === null
    ? legacySelection === null
    : legacySelection !== null && selectedAssetsEqual(first, legacySelection);
}

function isSelectedAssetRecord(value: unknown): value is DomainSelectedAsset {
  return isSelectedAsset(value) && value !== null;
}

function selectedAssetsEqual(left: DomainSelectedAsset, right: DomainSelectedAsset) {
  return left.id === right.id && left.title === right.title && left.media_type === right.media_type;
}

function isCourse(value: unknown) {
  return hasExactKeys(value, ["description", "id", "modules", "position", "title"]) &&
    UUID.test(String(value.id)) && typeof value.title === "string" &&
    typeof value.description === "string" && isPosition(value.position) &&
    Array.isArray(value.modules) && value.modules.every(isModule);
}

function isModule(value: unknown) {
  return hasExactKeys(value, ["id", "lessons", "position", "status", "title"]) &&
    UUID.test(String(value.id)) && typeof value.title === "string" &&
    value.status === "active" && isPosition(value.position) &&
    Array.isArray(value.lessons) && value.lessons.every(isLesson);
}

function isLesson(value: unknown) {
  return hasExactKeys(value, ["content", "id", "kind", "position", "status", "title"]) &&
    UUID.test(String(value.id)) && typeof value.title === "string" &&
    value.kind === "text" && typeof value.content === "string" &&
    value.status === "active" && isPosition(value.position);
}

function isLessonProgress(value: unknown) {
  return hasExactKeys(value, [
    "completed",
    "lesson_id",
    "progress_percent",
    "updated_at"
  ]) && UUID.test(String(value.lesson_id)) && isPercent(value.progress_percent) &&
    typeof value.completed === "boolean" &&
    (value.updated_at === null || typeof value.updated_at === "string");
}

function isUuidRecord(value: unknown, keys: string[]) {
  return hasExactKeys(value, keys) && keys.every((key) => UUID.test(String(value[key])));
}

function nullableUuid(value: unknown) {
  return value === null || (typeof value === "string" && UUID.test(value));
}

function isPercent(value: unknown) {
  return Number.isInteger(value) && Number(value) >= 0 && Number(value) <= 100;
}

function isPosition(value: unknown) {
  return Number.isInteger(value) && Number(value) >= 0;
}

function isVersion(value: unknown) {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

function hasExactKeys(value: unknown, expected: string[]): value is Record<string, unknown> {
  if (!isRecord(value)) return false;
  const actual = Object.keys(value).sort();
  const sorted = [...expected].sort();
  return actual.length === sorted.length && actual.every((key, index) => key === sorted[index]);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
