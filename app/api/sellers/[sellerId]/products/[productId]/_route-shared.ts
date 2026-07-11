import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import { DomainApiError } from "@/lib/internal-api-client.server";
import { PayloadTooLargeError, readLimitedForm } from "@/lib/limited-form.server";
import { oauthService } from "@/lib/oauth-service.server";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export interface ProductRouteContext {
  params: Promise<{ sellerId: string; productId: string }>;
}

export async function productRouteIds(context: ProductRouteContext) {
  const { sellerId, productId } = await context.params;
  return UUID.test(sellerId) && UUID.test(productId) ? { sellerId, productId } : null;
}

export async function authenticatedSession(request: NextRequest) {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId) return null;
  const session = await oauthService().getSession(sessionId);
  return session ? { sessionId, session } : null;
}

export type LimitedFieldsResult =
  | { kind: "ok"; fields: Record<string, string> }
  | { kind: "invalid" }
  | { kind: "too_large" };

export async function readExactLimitedFields(
  request: NextRequest,
  expectedNames: readonly string[] | readonly (readonly string[])[],
  maxBytes?: number
): Promise<LimitedFieldsResult> {
  let form: FormData | null;
  try {
    form = await readLimitedForm(request, maxBytes);
  } catch (error) {
    if (error instanceof PayloadTooLargeError) return { kind: "too_large" };
    return { kind: "invalid" };
  }
  if (!form) return { kind: "invalid" };

  const variants = expectedNames.length > 0 && Array.isArray(expectedNames[0])
    ? expectedNames as readonly (readonly string[])[]
    : [expectedNames as readonly string[]];
  const allowed = new Set(variants.flatMap((variant) => [...variant]));
  const fields: Record<string, string> = {};
  for (const [name, value] of form.entries()) {
    if (!allowed.has(name) || Object.hasOwn(fields, name) || typeof value !== "string") {
      return { kind: "invalid" };
    }
    fields[name] = value;
  }
  const actual = Object.keys(fields);
  return variants.some((variant) =>
    actual.length === variant.length && variant.every((name) => Object.hasOwn(fields, name))
  )
    ? { kind: "ok", fields }
    : { kind: "invalid" };
}

export function canonicalUuid(value: string | undefined) {
  return value !== undefined && UUID.test(value) ? value : null;
}

export function safeVersion(value: string | undefined, allowZero = false) {
  if (value === undefined || !/^(?:0|[1-9][0-9]*)$/.test(value)) return null;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < (allowZero ? 0 : 1)) return null;
  return parsed;
}

export function unauthorized() {
  return jsonError("unauthorized", 401);
}

export function forbiddenMutation() {
  return jsonError("forbidden_mutation", 403);
}

export function notFound() {
  return jsonError("product_not_found", 404);
}

export function invalidInput(field: string) {
  return NextResponse.json({ error: "invalid_product_input", field }, {
    status: 422,
    headers: { "cache-control": "no-store" }
  });
}

export function payloadTooLarge() {
  return jsonError("payload_too_large", 413);
}

export function mappedError(error: unknown) {
  if (isOAuthStoreUnavailable(error)) return jsonError("oauth_store_unavailable", 503);
  if (error instanceof DomainApiError) return jsonError(error.code, error.status);
  return jsonError("product_api_unavailable", 503);
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function jsonError(error: string, status: number) {
  return NextResponse.json({ error }, {
    status,
    headers: { "cache-control": "no-store" }
  });
}
