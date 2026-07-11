import { NextRequest, NextResponse } from "next/server";

import { readDomainCurriculum } from "@/lib/internal-api-client.server";

import { authenticatedSession, isRecord, mappedError, notFound, unauthorized } from "../../../_route-shared";
import {
  curriculumCsrf,
  curriculumRouteIds,
  type CurriculumRouteContext
} from "../_curriculum-route-shared";

export async function GET(request: NextRequest, context: CurriculumRouteContext) {
  const ids = await curriculumRouteIds(context);
  if (!ids) return notFound();
  try {
    const authenticated = await authenticatedSession(request);
    if (!authenticated) return unauthorized();
    const payload = await readDomainCurriculum(
      authenticated.sessionId,
      authenticated.session,
      ids.sellerId,
      ids.productId,
      ids.courseId
    );
    if (!isRecord(payload)) return mappedError(new Error("invalid domain response"));
    return NextResponse.json({ ...payload, csrf: curriculumCsrf(authenticated.sessionId) }, {
      headers: { "cache-control": "no-store" }
    });
  } catch (error) {
    return mappedError(error);
  }
}
