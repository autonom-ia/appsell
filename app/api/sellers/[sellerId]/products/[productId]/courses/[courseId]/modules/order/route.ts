import { NextRequest, NextResponse } from "next/server";

import { reorderDomainCourseModules } from "@/lib/internal-api-client.server";

import { canonicalUuid, mappedError, notFound, safeVersion } from "../../../../_route-shared";
import {
  curriculumActions,
  curriculumInvalidInput,
  curriculumRouteIds,
  parseOrderedIds,
  prepareCurriculumMutation,
  type CurriculumRouteContext
} from "../../_curriculum-route-shared";

const ORDER_FIELDS = ["_csrf", "idempotency_key", "expected_course_version", "ordered_ids"] as const;

export async function PUT(request: NextRequest, context: CurriculumRouteContext) {
  const ids = await curriculumRouteIds(context);
  if (!ids) return notFound();
  const prepared = await prepareCurriculumMutation(request, curriculumActions.reorder, ORDER_FIELDS);
  if (!prepared.ok) return prepared.response;
  const idempotencyKey = canonicalUuid(prepared.fields.idempotency_key);
  const expectedCourseVersion = safeVersion(prepared.fields.expected_course_version);
  const orderedIds = parseOrderedIds(prepared.fields.ordered_ids, 50);
  if (!idempotencyKey) return curriculumInvalidInput("idempotency_key");
  if (expectedCourseVersion === null) return curriculumInvalidInput("expected_course_version");
  if (!orderedIds) return curriculumInvalidInput("ordered_ids");
  try {
    const payload = await reorderDomainCourseModules(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      ids.sellerId,
      ids.productId,
      ids.courseId,
      { idempotencyKey, expectedCourseVersion, orderedIds }
    );
    return NextResponse.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}
