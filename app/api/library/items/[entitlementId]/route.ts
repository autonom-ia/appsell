import { NextRequest } from "next/server";

import { readDomainLearningResume } from "@/lib/internal-api-client.server";
import {
  authenticatedLearningSession,
  learningError,
  learningItemId,
  learningNotFound,
  learningPlayerResponse,
  learningUnauthorized,
  type LearningItemRouteContext
} from "../_route-shared";

export async function GET(request: NextRequest, context: LearningItemRouteContext) {
  try {
    const authenticated = await authenticatedLearningSession(request);
    if (!authenticated) return learningUnauthorized();
    const entitlementId = await learningItemId(context);
    if (!entitlementId) return learningNotFound();
    const payload = await readDomainLearningResume(
      authenticated.sessionId,
      authenticated.session,
      entitlementId
    );
    return learningPlayerResponse(payload, authenticated.sessionId);
  } catch (error) {
    return learningError(error);
  }
}
