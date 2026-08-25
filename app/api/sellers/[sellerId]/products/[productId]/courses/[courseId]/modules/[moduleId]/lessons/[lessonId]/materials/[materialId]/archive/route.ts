import { NextRequest, NextResponse } from "next/server";

import { archiveDomainLessonMaterial } from "@/lib/internal-api-client.server";

import {
  canonicalUuid,
  mappedError,
  notFound,
  safeVersion
} from "../../../../../../../../../_route-shared";
import {
  curriculumActions,
  curriculumInvalidInput,
  lessonRouteIds,
  prepareCurriculumMutation,
  type LessonRouteContext
} from "../../../../../../../_curriculum-route-shared";

const ARCHIVE_FIELDS = ["_csrf", "idempotency_key", "expected_course_version"] as const;

interface MaterialRouteContext extends LessonRouteContext {
  params: Promise<Awaited<LessonRouteContext["params"]> & { materialId: string }>;
}

export async function POST(request: NextRequest, context: MaterialRouteContext) {
  const values = await context.params;
  const ids = await lessonRouteIds({ params: Promise.resolve(values) });
  const materialId = canonicalUuid(values.materialId);
  if (!ids || !materialId) return notFound();
  const prepared = await prepareCurriculumMutation(request, curriculumActions.archive, ARCHIVE_FIELDS);
  if (!prepared.ok) return prepared.response;
  const idempotencyKey = canonicalUuid(prepared.fields.idempotency_key);
  const expectedCourseVersion = safeVersion(prepared.fields.expected_course_version);
  if (!idempotencyKey) return curriculumInvalidInput("idempotency_key");
  if (expectedCourseVersion === null) return curriculumInvalidInput("expected_course_version");
  try {
    const payload = await archiveDomainLessonMaterial(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      ids.sellerId,
      ids.productId,
      ids.courseId,
      ids.moduleId,
      ids.lessonId,
      materialId,
      { idempotencyKey, expectedCourseVersion }
    );
    return NextResponse.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}
