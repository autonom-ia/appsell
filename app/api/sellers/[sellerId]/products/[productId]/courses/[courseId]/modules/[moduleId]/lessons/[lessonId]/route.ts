import { NextRequest, NextResponse } from "next/server";

import { updateDomainCourseLesson } from "@/lib/internal-api-client.server";

import { canonicalUuid, mappedError, notFound, safeVersion } from "../../../../../../_route-shared";
import {
  curriculumActions,
  curriculumInvalidInput,
  lessonRouteIds,
  prepareCurriculumMutation,
  type LessonRouteContext,
  validCurriculumContent,
  validCurriculumTitle
} from "../../../../_curriculum-route-shared";

const UPDATE_FIELDS = [
  "_csrf", "idempotency_key", "expected_course_version", "title", "kind", "content"
] as const;

export async function PATCH(request: NextRequest, context: LessonRouteContext) {
  const ids = await lessonRouteIds(context);
  if (!ids) return notFound();
  const prepared = await prepareCurriculumMutation(request, curriculumActions.update, UPDATE_FIELDS);
  if (!prepared.ok) return prepared.response;
  const idempotencyKey = canonicalUuid(prepared.fields.idempotency_key);
  const expectedCourseVersion = safeVersion(prepared.fields.expected_course_version);
  if (!idempotencyKey) return curriculumInvalidInput("idempotency_key");
  if (expectedCourseVersion === null) return curriculumInvalidInput("expected_course_version");
  if (!validCurriculumTitle(prepared.fields.title)) return curriculumInvalidInput("title");
  if (prepared.fields.kind !== "text") return curriculumInvalidInput("kind");
  if (!validCurriculumContent(prepared.fields.content)) return curriculumInvalidInput("content");
  try {
    const payload = await updateDomainCourseLesson(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      ids.sellerId,
      ids.productId,
      ids.courseId,
      ids.moduleId,
      ids.lessonId,
      {
        idempotencyKey,
        expectedCourseVersion,
        title: prepared.fields.title,
        content: prepared.fields.content
      }
    );
    return NextResponse.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}
