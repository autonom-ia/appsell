import { NextRequest } from "next/server";

import { completeDomainLesson } from "@/lib/internal-api-client.server";
import {
  learningActions,
  learningError,
  learningJsonErrorForInvalidInput,
  learningLessonIds,
  learningNotFound,
  learningProgressResponse,
  prepareLearningMutation,
  validExpectedVersion,
  validIdempotencyKey,
  type LearningLessonRouteContext
} from "../../../../_route-shared";

const FIELDS = ["_csrf", "idempotency_key", "expected_version"] as const;

export async function POST(request: NextRequest, context: LearningLessonRouteContext) {
  const prepared = await prepareLearningMutation(request, learningActions.complete, FIELDS);
  if (!prepared.ok) return prepared.response;
  const ids = await learningLessonIds(context);
  if (!ids) return learningNotFound();
  const idempotencyKey = validIdempotencyKey(prepared.fields.idempotency_key);
  const expectedVersion = validExpectedVersion(prepared.fields.expected_version);
  if (!idempotencyKey || expectedVersion === null) {
    return learningJsonErrorForInvalidInput();
  }
  try {
    const payload = await completeDomainLesson(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      ids.entitlementId,
      ids.lessonId,
      { idempotencyKey, expectedVersion }
    );
    return learningProgressResponse(payload);
  } catch (error) {
    return learningError(error);
  }
}
