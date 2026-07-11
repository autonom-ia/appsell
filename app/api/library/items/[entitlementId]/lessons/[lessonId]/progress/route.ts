import { NextRequest } from "next/server";

import { updateDomainLessonProgress } from "@/lib/internal-api-client.server";
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
  validProgressPercent,
  type LearningLessonRouteContext
} from "../../../../_route-shared";

const FIELDS = ["_csrf", "idempotency_key", "progress_percent", "expected_version"] as const;

export async function PUT(request: NextRequest, context: LearningLessonRouteContext) {
  const prepared = await prepareLearningMutation(request, learningActions.progress, FIELDS);
  if (!prepared.ok) return prepared.response;
  const ids = await learningLessonIds(context);
  if (!ids) return learningNotFound();
  const idempotencyKey = validIdempotencyKey(prepared.fields.idempotency_key);
  const progressPercent = validProgressPercent(prepared.fields.progress_percent);
  const expectedVersion = validExpectedVersion(prepared.fields.expected_version);
  if (!idempotencyKey || progressPercent === null || expectedVersion === null) {
    return learningJsonErrorForInvalidInput();
  }
  try {
    const payload = await updateDomainLessonProgress(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      ids.entitlementId,
      ids.lessonId,
      { idempotencyKey, progressPercent, expectedVersion }
    );
    return learningProgressResponse(payload);
  } catch (error) {
    return learningError(error);
  }
}
