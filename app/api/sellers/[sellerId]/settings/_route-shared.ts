import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import type { SellerSettingsProjection } from "@/lib/internal-api-client.server";
import {
  CLEAR_SELLER_LOGO_CSRF_ACTION,
  CONFIRM_LOGO_UPLOAD_CSRF_ACTION,
  CREATE_LOGO_UPLOAD_CSRF_ACTION,
  UPDATE_SELLER_SETTINGS_CSRF_ACTION,
  csrfTokenForSession,
  verifyMutation
} from "@/lib/mutation-protection.server";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";
import { configuredStorageUploadOrigin, trustedProductCover } from "@/lib/storage-upload-policy.server";
import {
  authenticatedSession,
  canonicalUuid,
  forbiddenMutation,
  invalidInput,
  mappedError,
  payloadTooLarge,
  readExactLimitedFields,
  unauthorized
} from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";

// Configurações da loja (#352): a porta é a mesma das outras mutações do
// vendedor — origem configurada, sessão, token por ação, formulário com os
// campos exatos. Papel (dono/admin) é decidido pela função do banco; aqui não
// se adivinha.

export interface SettingsRouteContext {
  params: Promise<{ sellerId: string }>;
}

export interface LogoUploadRouteContext {
  params: Promise<{ sellerId: string; uploadId: string }>;
}

// Nome, tagline, sobre e cor: texto curto. Nada de imagem passa por aqui.
const MAX_FORM_BYTES = 4_096;

export const settingsActions = {
  update: UPDATE_SELLER_SETTINGS_CSRF_ACTION,
  logoIntent: CREATE_LOGO_UPLOAD_CSRF_ACTION,
  logoConfirm: CONFIRM_LOGO_UPLOAD_CSRF_ACTION,
  logoClear: CLEAR_SELLER_LOGO_CSRF_ACTION
} as const;

export function settingsCsrf(sessionId: string) {
  return {
    update: csrfTokenForSession(sessionId, settingsActions.update),
    logo_intent: csrfTokenForSession(sessionId, settingsActions.logoIntent),
    logo_confirm: csrfTokenForSession(sessionId, settingsActions.logoConfirm),
    logo_clear: csrfTokenForSession(sessionId, settingsActions.logoClear)
  };
}

export async function settingsSellerId(context: SettingsRouteContext) {
  const { sellerId } = await context.params;
  return canonicalUuid(sellerId);
}

export async function logoUploadIds(context: LogoUploadRouteContext) {
  const values = await context.params;
  const sellerId = canonicalUuid(values.sellerId);
  const uploadId = canonicalUuid(values.uploadId);
  return sellerId && uploadId ? { sellerId, uploadId } : null;
}

export async function prepareSettingsMutation(
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

/**
 * O que o navegador recebe das configurações.
 *
 * O logo passa pela mesma prova de endereço da capa: só sai se apontar para o
 * nosso armazenamento, assinado. Sem origem configurada não há logo para
 * mostrar, e a tela usa as iniciais.
 */
export function browserSettings(settings: SellerSettingsProjection) {
  let storageOrigin = "";
  try {
    storageOrigin = configuredStorageUploadOrigin();
  } catch {
    storageOrigin = "";
  }
  const trusted = storageOrigin && settings.logo ? trustedProductCover(settings.logo, storageOrigin) : null;
  const logo = trusted && typeof trusted === "object"
    ? {
        url: (trusted as { url: string }).url,
        media_type: (trusted as { media_type: string }).media_type,
        width: (trusted as { width: number }).width,
        height: (trusted as { height: number }).height
      }
    : null;
  return {
    id: settings.id,
    slug: settings.slug,
    display_name: settings.display_name,
    status: settings.status,
    tagline: settings.tagline ?? null,
    about: settings.about ?? null,
    accent_color: settings.accent_color ?? null,
    logo
  };
}

export function settingsJson(body: unknown, status = 200) {
  return NextResponse.json(body, { status, headers: { "cache-control": "no-store" } });
}
