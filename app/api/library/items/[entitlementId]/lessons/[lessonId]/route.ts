import { NextRequest } from "next/server";

import { readDomainLearningLesson } from "@/lib/internal-api-client.server";
import {
  authenticatedLearningSession,
  learningError,
  learningLessonIds,
  learningNotFound,
  learningPlayerResponse,
  learningUnauthorized,
  type LearningLessonRouteContext
} from "../../../_route-shared";

export async function GET(request: NextRequest, context: LearningLessonRouteContext) {
  try {
    const authenticated = await authenticatedLearningSession(request);
    if (!authenticated) return learningUnauthorized();
    const ids = await learningLessonIds(context);
    if (!ids) return learningNotFound();
    const payload = await readDomainLearningLesson(
      authenticated.sessionId,
      authenticated.session,
      ids.entitlementId,
      ids.lessonId
    );
    return learningPlayerResponse(payload, authenticated.sessionId, authenticated.session);
  } catch (error) {
    return learningError(error);
  }
}
