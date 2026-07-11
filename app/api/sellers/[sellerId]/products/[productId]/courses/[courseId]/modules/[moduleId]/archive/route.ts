import { NextRequest, NextResponse } from "next/server";

import { archiveDomainCourseModule } from "@/lib/internal-api-client.server";

import { canonicalUuid, mappedError, notFound, safeVersion } from "../../../../../_route-shared";
import {
  curriculumActions,
  curriculumInvalidInput,
  moduleRouteIds,
  prepareCurriculumMutation,
  type ModuleRouteContext
} from "../../../_curriculum-route-shared";

const ARCHIVE_FIELDS = ["_csrf", "idempotency_key", "expected_course_version"] as const;

export async function POST(request: NextRequest, context: ModuleRouteContext) {
  const ids = await moduleRouteIds(context);
  if (!ids) return notFound();
  const prepared = await prepareCurriculumMutation(request, curriculumActions.archive, ARCHIVE_FIELDS);
  if (!prepared.ok) return prepared.response;
  const idempotencyKey = canonicalUuid(prepared.fields.idempotency_key);
  const expectedCourseVersion = safeVersion(prepared.fields.expected_course_version);
  if (!idempotencyKey) return curriculumInvalidInput("idempotency_key");
  if (expectedCourseVersion === null) return curriculumInvalidInput("expected_course_version");
  try {
    const payload = await archiveDomainCourseModule(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      ids.sellerId,
      ids.productId,
      ids.courseId,
      ids.moduleId,
      { idempotencyKey, expectedCourseVersion }
    );
    return NextResponse.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}
