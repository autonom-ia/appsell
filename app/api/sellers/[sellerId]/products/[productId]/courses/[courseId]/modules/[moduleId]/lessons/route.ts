import { NextRequest, NextResponse } from "next/server";

import { createDomainCourseLesson } from "@/lib/internal-api-client.server";

import { canonicalUuid, mappedError, notFound, safeVersion } from "../../../../../_route-shared";
import {
  curriculumActions,
  curriculumInvalidInput,
  moduleRouteIds,
  prepareCurriculumMutation,
  type ModuleRouteContext,
  validCurriculumContent,
  validCurriculumTitle
} from "../../../_curriculum-route-shared";

const CREATE_FIELDS = [
  "_csrf", "idempotency_key", "expected_course_version", "title", "kind", "content"
] as const;

export async function POST(request: NextRequest, context: ModuleRouteContext) {
  const ids = await moduleRouteIds(context);
  if (!ids) return notFound();
  const prepared = await prepareCurriculumMutation(request, curriculumActions.create, CREATE_FIELDS);
  if (!prepared.ok) return prepared.response;
  const idempotencyKey = canonicalUuid(prepared.fields.idempotency_key);
  const expectedCourseVersion = safeVersion(prepared.fields.expected_course_version);
  if (!idempotencyKey) return curriculumInvalidInput("idempotency_key");
  if (expectedCourseVersion === null) return curriculumInvalidInput("expected_course_version");
  if (!validCurriculumTitle(prepared.fields.title)) return curriculumInvalidInput("title");
  if (prepared.fields.kind !== "text") return curriculumInvalidInput("kind");
  if (!validCurriculumContent(prepared.fields.content)) return curriculumInvalidInput("content");
  try {
    const payload = await createDomainCourseLesson(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      ids.sellerId,
      ids.productId,
      ids.courseId,
      ids.moduleId,
      {
        idempotencyKey,
        expectedCourseVersion,
        title: prepared.fields.title,
        content: prepared.fields.content
      }
    );
    return NextResponse.json(payload, {
      status: typeof payload === "object" && payload !== null && "created" in payload && payload.created ? 201 : 200,
      headers: { "cache-control": "no-store" }
    });
  } catch (error) {
    return mappedError(error);
  }
}
