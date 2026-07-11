import { NextRequest, NextResponse } from "next/server";

import { reorderDomainCourseLessons } from "@/lib/internal-api-client.server";

import { canonicalUuid, mappedError, notFound, safeVersion } from "../../../../../../_route-shared";
import {
  curriculumActions,
  curriculumInvalidInput,
  moduleRouteIds,
  parseOrderedIds,
  prepareCurriculumMutation,
  type ModuleRouteContext
} from "../../../../_curriculum-route-shared";

const ORDER_FIELDS = ["_csrf", "idempotency_key", "expected_course_version", "ordered_ids"] as const;

export async function PUT(request: NextRequest, context: ModuleRouteContext) {
  const ids = await moduleRouteIds(context);
  if (!ids) return notFound();
  const prepared = await prepareCurriculumMutation(request, curriculumActions.reorder, ORDER_FIELDS);
  if (!prepared.ok) return prepared.response;
  const idempotencyKey = canonicalUuid(prepared.fields.idempotency_key);
  const expectedCourseVersion = safeVersion(prepared.fields.expected_course_version);
  const orderedIds = parseOrderedIds(prepared.fields.ordered_ids, 100);
  if (!idempotencyKey) return curriculumInvalidInput("idempotency_key");
  if (expectedCourseVersion === null) return curriculumInvalidInput("expected_course_version");
  if (!orderedIds) return curriculumInvalidInput("ordered_ids");
  try {
    const payload = await reorderDomainCourseLessons(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      ids.sellerId,
      ids.productId,
      ids.courseId,
      ids.moduleId,
      { idempotencyKey, expectedCourseVersion, orderedIds }
    );
    return NextResponse.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}
