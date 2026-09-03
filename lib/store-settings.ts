/**
 * O contrato das configurações da loja entre o BFF e o navegador.
 *
 * O navegador nunca confia na forma do que chega: a mesma disciplina do
 * parser da vitrine. Aqui só há o que a tela precisa — chave e versão de
 * objeto do logo morreram na API.
 */

export interface StoreLogo {
  url: string;
  media_type: string;
  width: number;
  height: number;
}

export interface StoreSettings {
  id: string;
  slug: string;
  display_name: string;
  status: string;
  tagline: string | null;
  about: string | null;
  accent_color: string | null;
  logo: StoreLogo | null;
}

export interface StoreSettingsCsrf {
  update: string;
  logo_intent: string;
  logo_confirm: string;
  logo_clear: string;
}

export const STORE_LIMITS = {
  displayName: { min: 2, max: 120 },
  tagline: { min: 2, max: 160 },
  about: { min: 2, max: 600 },
  headline: { min: 2, max: 200 }
} as const;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const HEX = /^#[0-9a-f]{6}$/;

export function parseStoreSettings(value: unknown): StoreSettings | null {
  if (!isRecord(value)) return null;
  const { id, slug, display_name: displayName, status, tagline, about, accent_color: accent, logo } = value;
  if (typeof id !== "string" || !UUID.test(id)) return null;
  if (typeof slug !== "string" || !SLUG.test(slug)) return null;
  if (!isText(displayName, STORE_LIMITS.displayName.min, STORE_LIMITS.displayName.max)) return null;
  if (typeof status !== "string" || status.length === 0) return null;
  if (!isOptionalText(tagline, STORE_LIMITS.tagline.min, STORE_LIMITS.tagline.max)) return null;
  if (!isOptionalText(about, STORE_LIMITS.about.min, STORE_LIMITS.about.max)) return null;
  if (accent !== null && accent !== undefined && !(typeof accent === "string" && HEX.test(accent))) return null;
  const parsedLogo = logo === null || logo === undefined ? null : parseStoreLogo(logo);
  if (logo && !parsedLogo) return null;
  return {
    id,
    slug,
    display_name: displayName,
    status,
    tagline: (tagline as string | null | undefined) ?? null,
    about: (about as string | null | undefined) ?? null,
    accent_color: (accent as string | null | undefined) ?? null,
    logo: parsedLogo
  };
}

export function parseStoreLogo(value: unknown): StoreLogo | null {
  if (!isRecord(value)) return null;
  const { url, media_type: mediaType, width, height } = value;
  if (typeof url !== "string" || !url.startsWith("https://")) return null;
  if (typeof mediaType !== "string" || !mediaType.startsWith("image/")) return null;
  if (!isDimension(width) || !isDimension(height)) return null;
  return { url, media_type: mediaType, width, height };
}

/** A forma do formulário, com vazio virando ausência. */
export function normalizeOptionalText(value: string | null | undefined): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

function isText(value: unknown, min: number, max: number): value is string {
  return typeof value === "string" && value === value.trim() && value.length >= min && value.length <= max;
}

function isOptionalText(value: unknown, min: number, max: number): boolean {
  return value === null || value === undefined || isText(value, min, max);
}

function isDimension(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 4000;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
