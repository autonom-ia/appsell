import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import {
  ARCHIVE_CURRICULUM_CSRF_ACTION,
  CREATE_CURRICULUM_CSRF_ACTION,
  REORDER_CURRICULUM_CSRF_ACTION,
  UPDATE_CURRICULUM_CSRF_ACTION,
  csrfTokenForSession,
  verifyMutation
} from "@/lib/mutation-protection.server";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";

import {
  authenticatedSession,
  canonicalUuid,
  forbiddenMutation,
  invalidInput,
  mappedError,
  payloadTooLarge,
  readExactLimitedFields,
  unauthorized
} from "../../_route-shared";

const CONTROL = /[\p{Cc}\p{Cs}]/u;
const CURRICULUM_FORM_MAX_BYTES = 32_768;

export interface CurriculumRouteContext {
  params: Promise<{ sellerId: string; productId: string; courseId: string }>;
}

export interface ModuleRouteContext {
  params: Promise<{ sellerId: string; productId: string; courseId: string; moduleId: string }>;
}

export interface LessonRouteContext {
  params: Promise<{
    sellerId: string;
    productId: string;
    courseId: string;
    moduleId: string;
    lessonId: string;
  }>;
}

export async function curriculumRouteIds(context: CurriculumRouteContext) {
  const values = await context.params;
  const sellerId = canonicalUuid(values.sellerId);
  const productId = canonicalUuid(values.productId);
  const courseId = canonicalUuid(values.courseId);
  return sellerId && productId && courseId ? { sellerId, productId, courseId } : null;
}

export async function moduleRouteIds(context: ModuleRouteContext) {
  const values = await context.params;
  const base = await curriculumRouteIds({ params: Promise.resolve(values) });
  const moduleId = canonicalUuid(values.moduleId);
  return base && moduleId ? { ...base, moduleId } : null;
}

export async function lessonRouteIds(context: LessonRouteContext) {
  const values = await context.params;
  const base = await moduleRouteIds({ params: Promise.resolve(values) });
  const lessonId = canonicalUuid(values.lessonId);
  return base && lessonId ? { ...base, lessonId } : null;
}

export function curriculumCsrf(sessionId: string) {
  return {
    create: csrfTokenForSession(sessionId, CREATE_CURRICULUM_CSRF_ACTION),
    update: csrfTokenForSession(sessionId, UPDATE_CURRICULUM_CSRF_ACTION),
    reorder: csrfTokenForSession(sessionId, REORDER_CURRICULUM_CSRF_ACTION),
    archive: csrfTokenForSession(sessionId, ARCHIVE_CURRICULUM_CSRF_ACTION)
  };
}

export async function prepareCurriculumMutation(
  request: NextRequest,
  action: string,
  expectedFields: readonly string[]
) {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId || !hasConfiguredOrigin(request, configuredPublicOrigin())) {
    return { ok: false as const, response: forbiddenMutation() };
  }

  let authenticated: Awaited<ReturnType<typeof authenticatedSession>>;
  try {
    authenticated = await authenticatedSession(request);
  } catch (error) {
    return { ok: false as const, response: mappedError(error) };
  }
  if (!authenticated) return { ok: false as const, response: unauthorized() };

  const parsed = await readExactLimitedFields(
    request,
    expectedFields,
    CURRICULUM_FORM_MAX_BYTES
  );
  if (parsed.kind === "too_large") {
    return { ok: false as const, response: payloadTooLarge() };
  }
  if (parsed.kind === "invalid") {
    return { ok: false as const, response: invalidInput("form") };
  }
  if (!verifyMutation(request, sessionId, action, parsed.fields._csrf)) {
    return { ok: false as const, response: forbiddenMutation() };
  }
  return { ok: true as const, authenticated, fields: parsed.fields };
}

export function validCurriculumTitle(value: string | undefined): value is string {
  if (value === undefined || value !== value.trim() || CONTROL.test(value)) return false;
  const bytes = Buffer.byteLength(value);
  return bytes >= 2 && bytes <= 160;
}

export function validCurriculumContent(value: string | undefined): value is string {
  if (value === undefined || Buffer.byteLength(value) > 8_192) return false;
  return !CONTROL.test(value.replace(/[\n\r\t]/g, ""));
}

export function parseOrderedIds(value: string | undefined, maximum: number) {
  if (value === undefined) return null;
  try {
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed) || parsed.length > maximum) return null;
    const ids = parsed.map((entry) => typeof entry === "string" ? canonicalUuid(entry) : null);
    if (ids.some((entry) => entry === null)) return null;
    const canonical = ids as string[];
    return new Set(canonical).size === canonical.length ? canonical : null;
  } catch {
    return null;
  }
}

export function curriculumInvalidInput(field: string) {
  return NextResponse.json({ error: "invalid_curriculum_input", field }, {
    status: 422,
    headers: { "cache-control": "no-store" }
  });
}

export const curriculumActions = {
  create: CREATE_CURRICULUM_CSRF_ACTION,
  update: UPDATE_CURRICULUM_CSRF_ACTION,
  reorder: REORDER_CURRICULUM_CSRF_ACTION,
  archive: ARCHIVE_CURRICULUM_CSRF_ACTION
} as const;
