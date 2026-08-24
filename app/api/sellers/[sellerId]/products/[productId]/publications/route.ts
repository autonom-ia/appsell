import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import { publishDomainProductDraft } from "@/lib/internal-api-client.server";
import {
  PUBLISH_PRODUCT_DRAFT_CSRF_ACTION,
  verifyMutation
} from "@/lib/mutation-protection.server";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";

import {
  authenticatedSession,
  canonicalUuid,
  forbiddenMutation,
  invalidInput,
  isRecord,
  mappedError,
  payloadTooLarge,
  productRouteIds,
  type ProductRouteContext,
  readExactLimitedFields,
  safeVersion,
  unauthorized
} from "../_route-shared";

const LEGACY_PUBLISH_FIELDS = [
  "_csrf",
  "idempotency_key",
  "course_id",
  "offer_id",
  "expected_product_version",
  "expected_course_version",
  "expected_offer_version"
] as const;
const MULTI_COURSE_PUBLISH_FIELDS = [
  "_csrf",
  "idempotency_key",
  "offer_id",
  "expected_product_version",
  "expected_offer_version",
  "course_versions"
] as const;
// Publishing now carries the access decision. The variants without the field
// stay accepted so a client that has not been updated keeps publishing free
// access instead of failing.
const LEGACY_PUBLISH_WITH_ACCESS_FIELDS = [
  ...LEGACY_PUBLISH_FIELDS,
  "access_mode"
] as const;
const MULTI_COURSE_PUBLISH_WITH_ACCESS_FIELDS = [
  ...MULTI_COURSE_PUBLISH_FIELDS,
  "access_mode"
] as const;

export async function POST(request: NextRequest, context: ProductRouteContext) {
  const ids = await productRouteIds(context);
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!ids || !sessionId || !hasConfiguredOrigin(request, configuredPublicOrigin())) {
    return forbiddenMutation();
  }

  let authenticated: Awaited<ReturnType<typeof authenticatedSession>>;
  try {
    authenticated = await authenticatedSession(request);
  } catch (error) {
    return mappedError(error);
  }
  if (!authenticated) return unauthorized();

  const parsed = await readExactLimitedFields(request, [
    LEGACY_PUBLISH_FIELDS,
    MULTI_COURSE_PUBLISH_FIELDS,
    LEGACY_PUBLISH_WITH_ACCESS_FIELDS,
    MULTI_COURSE_PUBLISH_WITH_ACCESS_FIELDS
  ]);
  if (parsed.kind === "too_large") return payloadTooLarge();
  if (parsed.kind === "invalid") return invalidInput("form");
  const fields = parsed.fields;
  if (!verifyMutation(
    request,
    sessionId,
    PUBLISH_PRODUCT_DRAFT_CSRF_ACTION,
    fields._csrf
  )) return forbiddenMutation();

  const idempotencyKey = canonicalUuid(fields.idempotency_key);
  const offerId = canonicalUuid(fields.offer_id);
  const productVersion = safeVersion(fields.expected_product_version);
  const offerVersion = safeVersion(fields.expected_offer_version);
  if (!idempotencyKey) return invalidInput("form");
  if (!offerId) return invalidInput("offer_id");
  if (productVersion === null) return invalidInput("expected_product_version");
  if (offerVersion === null) return invalidInput("expected_offer_version");
  const accessMode = fields.access_mode ?? "free";
  if (accessMode !== "free" && accessMode !== "paid") return invalidInput("access_mode");

  try {
    const courseVersions = fields.course_versions === undefined
      ? legacyCourseVersions(fields)
      : parseCourseVersions(fields.course_versions);
    if (!courseVersions) return invalidInput("course_versions");

    const payload = await publishDomainProductDraft(
      authenticated.sessionId,
      authenticated.session,
      ids.sellerId,
      ids.productId,
      {
        idempotencyKey,
        offerId,
        expectedProductVersion: productVersion,
        expectedOfferVersion: offerVersion,
        courseVersions,
        accessMode
      }
    );
    const created = isRecord(payload) && payload.created === true;
    return NextResponse.json(payload, {
      status: created ? 201 : 200,
      headers: { "cache-control": "no-store" }
    });
  } catch (error) {
    return mappedError(error);
  }
}

function legacyCourseVersions(fields: Record<string, string>) {
  const id = canonicalUuid(fields.course_id);
  const version = safeVersion(fields.expected_course_version);
  return id && version !== null ? [{ id, version }] : null;
}

function parseCourseVersions(serialized: string) {
  let parsed: unknown;
  try {
    parsed = JSON.parse(serialized);
  } catch {
    return null;
  }
  if (!Array.isArray(parsed) || parsed.length < 1 || parsed.length > 100) return null;
  const result: Array<{ id: string; version: number }> = [];
  const ids = new Set<string>();
  for (const entry of parsed) {
    if (!isRecord(entry) || Object.keys(entry).sort().join(",") !== "id,version") return null;
    const id = typeof entry.id === "string" ? canonicalUuid(entry.id) : null;
    const version = typeof entry.version === "number" && Number.isSafeInteger(entry.version) &&
      entry.version >= 1 ? entry.version : null;
    if (!id || version === null || ids.has(id)) return null;
    ids.add(id);
    result.push({ id, version });
  }
  return result;
}
