import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import { DomainApiError } from "@/lib/internal-api-client.server";
import { learningLicenseLabel } from "@/lib/learning-license.server";
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
  content_role?: "course_content" | "support_material";
  download_allowed?: boolean;
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

export interface LearningAssetPageRouteContext {
  params: Promise<{ entitlementId: string; assetId: string; page: string }>;
}

export interface LearningAssetLinkRouteContext {
  params: Promise<{ entitlementId: string; assetId: string; linkId: string }>;
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

export async function learningAssetPageIds(context: LearningAssetPageRouteContext) {
  const { entitlementId, assetId, page } = await context.params;
  const canonicalEntitlement = canonicalUuid(entitlementId);
  const canonicalAsset = canonicalUuid(assetId);
  const canonicalPage = safeInteger(page, 1, 100);
  return canonicalEntitlement && canonicalAsset && canonicalPage !== null
    ? { entitlementId: canonicalEntitlement, assetId: canonicalAsset, page: canonicalPage }
    : null;
}

export async function learningAssetLinkIds(context: LearningAssetLinkRouteContext) {
  const { entitlementId, assetId, linkId } = await context.params;
  const canonicalEntitlement = canonicalUuid(entitlementId);
  const canonicalAsset = canonicalUuid(assetId);
  return canonicalEntitlement && canonicalAsset && /^[0-9a-f]{16}$/.test(linkId)
    ? { entitlementId: canonicalEntitlement, assetId: canonicalAsset, linkId }
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

export function learningPlayerResponse(
  payload: unknown,
  sessionId: string,
  viewer: {
    subject: string;
    identity?: { email: string; emailVerified: true } | null;
  }
) {
  if (!isLearningPlayerEnvelope(payload)) {
    return learningJsonError("invalid_learning_response", 503);
  }
  const domainAssets = payload.player.selected_assets ??
    (payload.player.selected_asset === null ? [] : [payload.player.selected_asset]);
  const selectedAssets = domainAssets.map((asset) => ({
    ...learningAssetLinks(String(payload.player.entitlement_id), asset),
    title: learningAssetDisplayTitle(payload.player, asset),
    media_type: asset.media_type,
    content_role: asset.content_role ?? "course_content",
    download_allowed: asset.download_allowed === true
  }));
  const player = {
    ...payload.player,
    selected_asset: selectedAssets[0] ?? null,
    selected_assets: selectedAssets,
    watermark: learningWatermark(payload.player, viewer)
  };
  return NextResponse.json({ player, csrf: learningCsrf(sessionId) }, {
    headers: NO_STORE_HEADERS
  });
}

function learningAssetDisplayTitle(
  player: LearningPlayerEnvelope["player"],
  asset: DomainSelectedAsset
) {
  const selectedLessonId = typeof player.selected_lesson_id === "string"
    ? player.selected_lesson_id
    : null;
  const course = isRecord(player.course) ? player.course : null;
  const modules = course && Array.isArray(course.modules) ? course.modules : [];
  for (const courseModule of modules) {
    if (!isRecord(courseModule) || !Array.isArray(courseModule.lessons)) continue;
    for (const lesson of courseModule.lessons) {
      if (!isRecord(lesson) || lesson.id !== selectedLessonId) continue;
      if (typeof lesson.title === "string" && lesson.title.trim()) return lesson.title;
    }
  }
  return asset.content_role === "support_material" ? "Material de apoio" : "Conteúdo do curso";
}

function learningWatermark(
  player: LearningPlayerEnvelope["player"],
  viewer: { subject: string; identity?: { email: string; emailVerified: true } | null }
) {
  const entitlementId = String(player.entitlement_id);
  return { label: learningLicenseLabel(viewer, entitlementId) };
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

export function learningForbiddenMutation() {
  return learningJsonError("forbidden_mutation", 403);
}

/**
 * Registra por que a leitura da área do aluno falhou, antes de a resposta virar
 * um código grosso.
 *
 * A resposta ao aluno é deliberadamente vaga — ele não pode descobrir, pelo
 * erro, o que existe do outro lado. Mas o servidor precisa saber, e até
 * 30/08/2026 não sabia: o motivo era descartado aqui e no `except` equivalente
 * da API. Uma investigação inteira não chegou ao motivo imediato porque as duas
 * camadas o jogavam fora, e a tela ainda culpava a conexão do aluno.
 *
 * Só a classe do erro e o status recebido da API entram. Nenhum valor.
 */
// `DomainApiError.code` e o `detail` da resposta, repassado sem validacao. Hoje
// os detalhes sao codigos fixos, mas nada obriga isso a continuar: uma rota nova,
// um proxy ou uma mensagem de erro do driver colocariam texto livre ali, e o log
// gravaria por inteiro. Registrar so o que reconhecemos mantem a promessa de
// "classe e status, nenhum valor" independente do que chegue.
const CODIGOS_CONHECIDOS = new Set([
  "learning_unavailable",
  "learning_item_unavailable",
  "invalid_internal_request",
  "invalid_domain_request",
  "offer_requires_checkout",
  "access_link_exhausted",
  "access_link_unavailable",
  "idempotency_conflict",
  "progress_version_conflict",
  "pdf_study_revision_conflict",
  "payload_too_large",
  "rate_limited"
]);

function reportLearningFailure(reason: string, status?: number): void {
  console.error(JSON.stringify({
    event: "learning_request_failed",
    reason,
    ...(status === undefined ? {} : { upstream_status: status })
  }));
}

export function learningError(error: unknown) {
  if (isOAuthStoreUnavailable(error)) {
    reportLearningFailure("oauth_store_unavailable");
    return learningJsonError("oauth_store_unavailable", 503);
  }
  if (error instanceof DomainApiError) {
    if (error.status === 403 || error.status === 404) return learningNotFound();
    // 401 e 403 da API significam que o envelope interno foi recusado — um
    // problema entre BFF e API, não uma indisponibilidade. Sem esta linha os
    // dois eram indistinguíveis, e a tela dizia ao aluno para verificar a
    // conexão dele.
    if (error.status === 401) {
      reportLearningFailure("internal_request_rejected", error.status);
      return learningJsonError("learning_unavailable", 503);
    }
    if (error.status === 409 && [
      "idempotency_conflict",
      "progress_version_conflict",
      "pdf_study_revision_conflict"
    ].includes(error.code)) {
      return learningJsonError(error.code, 409);
    }
    if (error.status === 413) return learningJsonError("payload_too_large", 413);
    if (error.status === 422) return learningJsonError("invalid_request_body", 422);
    if (error.status === 429) return learningJsonError("rate_limited", 429);
  }
  if (error instanceof DomainApiError) {
    const conhecido = typeof error.code === "string" && CODIGOS_CONHECIDOS.has(error.code);
    reportLearningFailure(
      conhecido ? `domain_api_${error.code}` : "domain_api_codigo_desconhecido",
      error.status
    );
  } else {
    reportLearningFailure("erro_nao_classificado");
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
  const hasPolicyContract = Object.hasOwn(player, "schema_version");
  if (!hasExactKeys(player, hasMultiAssetContract
    ? [...playerKeys, "selected_assets", ...(hasPolicyContract ? ["schema_version"] : [])]
    : playerKeys)) return false;
  if (hasPolicyContract && player.schema_version !== 3) return false;
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
  if (value === null) return true;
  if (!isRecord(value)) return false;
  const policy = Object.hasOwn(value, "content_role") || Object.hasOwn(value, "download_allowed");
  return (
    hasExactKeys(value, policy
      ? ["content_role", "download_allowed", "id", "media_type", "title"]
      : ["id", "media_type", "title"]) &&
    typeof value.id === "string" && UUID.test(value.id) &&
    typeof value.title === "string" && value.title.length > 0 &&
    (value.media_type === "application/pdf" || value.media_type === "video/mp4") &&
    (!policy || (
      (value.content_role === "course_content" || value.content_role === "support_material") &&
      typeof value.download_allowed === "boolean" &&
      (!value.download_allowed || (
        value.content_role === "support_material" && value.media_type === "application/pdf"
      ))
    ))
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

function learningAssetLinks(entitlementId: string, asset: DomainSelectedAsset) {
  const assetHref = `/api/library/items/${encodeURIComponent(entitlementId)}` +
    `/assets/${encodeURIComponent(asset.id)}`;
  const viewHref = asset.media_type === "application/pdf" ? `${assetHref}/pages` : assetHref;
  return {
    view_href: viewHref,
    download_href: asset.content_role === "support_material" &&
      asset.download_allowed === true && asset.media_type === "application/pdf"
      ? `${assetHref}/download`
      : null
  };
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
