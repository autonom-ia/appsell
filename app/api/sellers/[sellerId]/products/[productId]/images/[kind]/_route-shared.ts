import { NextRequest } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import {
  CLEAR_PRODUCT_COVER_CSRF_ACTION,
  CONFIRM_COVER_UPLOAD_CSRF_ACTION,
  CREATE_COVER_UPLOAD_CSRF_ACTION,
  csrfTokenForSession,
  verifyMutation
} from "@/lib/mutation-protection.server";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";
import type { ProductImageKind } from "@/lib/internal-api-client.server";
import {
  authenticatedSession,
  canonicalUuid,
  forbiddenMutation,
  invalidInput,
  mappedError,
  payloadTooLarge,
  readExactLimitedFields,
  safeVersion,
  unauthorized
} from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";

const SHA256 = /^[0-9a-f]{64}$/;
const COVER_MEDIA = new Set(["image/jpeg", "image/png", "image/webp"]);
const COVER_MAX_BYTES = 5 * 1024 * 1024;
// O formulário carrega identificadores e números, nunca a imagem. Os bytes vão
// direto do navegador para o armazenamento, então este limite é folgado de
// propósito: qualquer corpo maior do que isto está errado, não grande.
const MAX_FORM_BYTES = 4_096;

export interface CoverRouteContext {
  params: Promise<{ sellerId: string; productId: string; kind: string }>;
}

export interface CoverUploadRouteContext {
  params: Promise<{ sellerId: string; productId: string; kind: string; uploadId: string }>;
}

/** As duas imagens do produto (0056). Qualquer outro tipo é rota inexistente. */
const TIPOS = new Set<ProductImageKind>(["cover", "banner"]);

function tipo(valor: string): ProductImageKind | null {
  return TIPOS.has(valor as ProductImageKind) ? (valor as ProductImageKind) : null;
}

export const coverActions = {
  intent: CREATE_COVER_UPLOAD_CSRF_ACTION,
  confirm: CONFIRM_COVER_UPLOAD_CSRF_ACTION,
  clear: CLEAR_PRODUCT_COVER_CSRF_ACTION
} as const;

export function coverCsrf(sessionId: string) {
  return {
    intent: csrfTokenForSession(sessionId, coverActions.intent),
    confirm: csrfTokenForSession(sessionId, coverActions.confirm),
    clear: csrfTokenForSession(sessionId, coverActions.clear)
  };
}

export async function coverIds(context: CoverRouteContext) {
  const values = await context.params;
  const sellerOrganizationId = canonicalUuid(values.sellerId);
  const productId = canonicalUuid(values.productId);
  const kind = tipo(values.kind);
  return sellerOrganizationId && productId && kind
    ? { sellerOrganizationId, productId, kind }
    : null;
}

export async function coverUploadIds(context: CoverUploadRouteContext) {
  const values = await context.params;
  const base = await coverIds({ params: Promise.resolve(values) });
  const uploadId = canonicalUuid(values.uploadId);
  return base && uploadId ? { ...base, uploadId } : null;
}

export async function prepareCoverMutation(
  request: NextRequest,
  action: string,
  fields: readonly string[]
) {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId || !hasConfiguredOrigin(request, configuredPublicOrigin())) {
    return { ok: false as const, response: forbiddenMutation() };
  }
  let authenticated;
  try {
    authenticated = await authenticatedSession(request);
  } catch (error) {
    return { ok: false as const, response: mappedError(error) };
  }
  if (!authenticated) return { ok: false as const, response: unauthorized() };
  const parsed = await readExactLimitedFields(request, fields, MAX_FORM_BYTES);
  if (parsed.kind === "too_large") return { ok: false as const, response: payloadTooLarge() };
  if (parsed.kind === "invalid") return { ok: false as const, response: invalidInput("form") };
  if (!verifyMutation(request, sessionId, action, parsed.fields._csrf)) {
    return { ok: false as const, response: forbiddenMutation() };
  }
  return { ok: true as const, authenticated, fields: parsed.fields };
}

export function parseCoverIntent(fields: Record<string, string>) {
  const idempotencyKey = canonicalUuid(fields.idempotency_key);
  const byteSize = safeVersion(fields.byte_size);
  const mediaType = fields.media_type;
  const sha256 = fields.sha256;
  if (
    !idempotencyKey ||
    byteSize === null ||
    byteSize > COVER_MAX_BYTES ||
    !COVER_MEDIA.has(mediaType) ||
    !SHA256.test(sha256)
  ) return null;
  return { idempotencyKey, mediaType, byteSize, sha256 };
}

export function parseCoverMutation(fields: Record<string, string>) {
  const idempotencyKey = canonicalUuid(fields.idempotency_key);
  const expectedProductVersion = safeVersion(fields.expected_product_version);
  if (!idempotencyKey || expectedProductVersion === null) return null;
  return { idempotencyKey, expectedProductVersion };
}
