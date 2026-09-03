import { NextRequest, NextResponse } from "next/server";

import {
  readDomainSellerSettings,
  updateDomainSellerSettings
} from "@/lib/internal-api-client.server";
import { accentRejectionMessage, isAccentReadable, normalizeAccentColor } from "@/lib/store-brand";
import { STORE_LIMITS, normalizeOptionalText } from "@/lib/store-settings";
import {
  authenticatedSession,
  mappedError,
  notFound,
  unauthorized
} from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import {
  browserSettings,
  prepareSettingsMutation,
  settingsActions,
  settingsCsrf,
  settingsJson,
  settingsSellerId,
  type SettingsRouteContext
} from "./_route-shared";

const UPDATE_FIELDS = ["_csrf", "display_name", "tagline", "about", "accent_color"] as const;
const CONTROL = /[\p{Cc}\p{Cs}]/u;

export async function GET(request: NextRequest, context: SettingsRouteContext) {
  const sellerId = await settingsSellerId(context);
  if (!sellerId) return notFound();
  try {
    const authenticated = await authenticatedSession(request);
    if (!authenticated) return unauthorized();
    const settings = await readDomainSellerSettings(
      authenticated.sessionId,
      authenticated.session,
      sellerId
    );
    return settingsJson({
      settings: browserSettings(settings),
      csrf: settingsCsrf(authenticated.sessionId)
    });
  } catch (error) {
    return mappedError(error);
  }
}

export async function PATCH(request: NextRequest, context: SettingsRouteContext) {
  const sellerId = await settingsSellerId(context);
  if (!sellerId) return notFound();
  const prepared = await prepareSettingsMutation(request, settingsActions.update, UPDATE_FIELDS);
  if (!prepared.ok) return prepared.response;
  const fields = prepared.fields;

  const displayName = fields.display_name.trim();
  if (
    displayName.length < STORE_LIMITS.displayName.min ||
    displayName.length > STORE_LIMITS.displayName.max ||
    CONTROL.test(displayName)
  ) return invalidField("display_name");
  const tagline = normalizeOptionalText(fields.tagline);
  if (tagline !== null && !fits(tagline, STORE_LIMITS.tagline.min, STORE_LIMITS.tagline.max)) {
    return invalidField("tagline");
  }
  const about = normalizeOptionalText(fields.about);
  if (about !== null && !fits(about, STORE_LIMITS.about.min, STORE_LIMITS.about.max)) {
    return invalidField("about");
  }
  const rawAccent = fields.accent_color.trim();
  const accentColor = normalizeAccentColor(rawAccent);
  if (rawAccent !== "" && accentColor === null) return invalidField("accent_color");
  // A guarda de legibilidade, ao gravar. A página repete a conta ao pintar.
  if (accentColor !== null && !isAccentReadable(accentColor)) {
    return NextResponse.json(
      { error: "accent_contrast", field: "accent_color", message: accentRejectionMessage(accentColor) },
      { status: 422, headers: { "cache-control": "no-store" } }
    );
  }

  try {
    const settings = await updateDomainSellerSettings(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      sellerId,
      { displayName, tagline, about, accentColor }
    );
    return settingsJson({ settings: browserSettings(settings), changed: settings.changed === true });
  } catch (error) {
    return mappedError(error);
  }
}

function fits(value: string, min: number, max: number) {
  return value.length >= min && value.length <= max && !CONTROL.test(value);
}

function invalidField(field: string) {
  return NextResponse.json(
    { error: "invalid_store_input", field },
    { status: 422, headers: { "cache-control": "no-store" } }
  );
}
