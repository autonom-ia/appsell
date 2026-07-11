import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import {
  readDomainProductDraft,
  updateDomainProductDraft
} from "@/lib/internal-api-client.server";
import {
  csrfTokenForSession,
  PUBLISH_PRODUCT_DRAFT_CSRF_ACTION,
  UPDATE_PRODUCT_DRAFT_CSRF_ACTION,
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
  notFound,
  payloadTooLarge,
  productRouteIds,
  type ProductRouteContext,
  readExactLimitedFields,
  safeVersion,
  unauthorized
} from "./_route-shared";

const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const CONTROL = /[\p{Cc}\p{Cs}]/u;
const UPDATE_FIELDS = [
  "_csrf",
  "idempotency_key",
  "course_id",
  "offer_id",
  "expected_product_version",
  "expected_course_version",
  "expected_offer_version",
  "slug",
  "product_title",
  "product_description",
  "course_title",
  "offer_title",
  "offer_summary"
] as const;

export async function GET(request: NextRequest, context: ProductRouteContext) {
  const ids = await productRouteIds(context);
  if (!ids) return notFound();
  try {
    const authenticated = await authenticatedSession(request);
    if (!authenticated) return unauthorized();
    const payload = await readDomainProductDraft(
      authenticated.sessionId,
      authenticated.session,
      ids.sellerId,
      ids.productId
    );
    if (!isRecord(payload)) return mappedError(new Error("invalid domain response"));
    return NextResponse.json({
      ...payload,
      csrf: {
        update: csrfTokenForSession(authenticated.sessionId, UPDATE_PRODUCT_DRAFT_CSRF_ACTION),
        publish: csrfTokenForSession(authenticated.sessionId, PUBLISH_PRODUCT_DRAFT_CSRF_ACTION)
      }
    }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}

export async function PATCH(request: NextRequest, context: ProductRouteContext) {
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

  const parsed = await readExactLimitedFields(request, UPDATE_FIELDS);
  if (parsed.kind === "too_large") return payloadTooLarge();
  if (parsed.kind === "invalid") return invalidInput("form");
  const fields = parsed.fields;
  if (!verifyMutation(
    request,
    sessionId,
    UPDATE_PRODUCT_DRAFT_CSRF_ACTION,
    fields._csrf
  )) return forbiddenMutation();

  const idempotencyKey = canonicalUuid(fields.idempotency_key);
  const courseId = canonicalUuid(fields.course_id);
  const offerId = canonicalUuid(fields.offer_id);
  const productVersion = safeVersion(fields.expected_product_version);
  const courseVersion = safeVersion(fields.expected_course_version);
  const offerVersion = safeVersion(fields.expected_offer_version, true);
  if (!idempotencyKey) return invalidInput("form");
  if (!courseId) return invalidInput("course_id");
  if (!offerId) return invalidInput("offer_id");
  if (productVersion === null) return invalidInput("expected_product_version");
  if (courseVersion === null) return invalidInput("expected_course_version");
  if (offerVersion === null) return invalidInput("expected_offer_version");
  if (!validSlug(fields.slug)) return invalidInput("slug");
  if (!validTrimmedText(fields.product_title, 2, 160)) return invalidInput("product_title");
  if (!validText(fields.product_description, 2_000)) return invalidInput("product_description");
  if (!validTrimmedText(fields.course_title, 2, 160)) return invalidInput("course_title");
  if (!validTrimmedText(fields.offer_title, 2, 120)) return invalidInput("offer_title");
  if (!validText(fields.offer_summary, 500)) return invalidInput("offer_summary");

  try {
    const payload = await updateDomainProductDraft(
      authenticated.sessionId,
      authenticated.session,
      ids.sellerId,
      ids.productId,
      {
        idempotencyKey,
        courseId,
        offerId,
        expectedProductVersion: productVersion,
        expectedCourseVersion: courseVersion,
        expectedOfferVersion: offerVersion,
        slug: fields.slug!,
        productTitle: fields.product_title!,
        productDescription: fields.product_description!,
        courseTitle: fields.course_title!,
        offerTitle: fields.offer_title!,
        offerSummary: fields.offer_summary!
      }
    );
    return NextResponse.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}

function validSlug(value: string | undefined): value is string {
  if (value === undefined) return false;
  const bytes = Buffer.byteLength(value);
  return bytes >= 3 && bytes <= 63 && SLUG.test(value);
}

function validTrimmedText(
  value: string | undefined,
  minimum: number,
  maximum: number
): value is string {
  if (value === undefined || value !== value.trim() || CONTROL.test(value)) return false;
  const bytes = Buffer.byteLength(value);
  return bytes >= minimum && bytes <= maximum;
}

function validText(value: string | undefined, maximum: number): value is string {
  return value !== undefined && Buffer.byteLength(value) <= maximum && !CONTROL.test(value);
}
