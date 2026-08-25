import { NextRequest, NextResponse } from "next/server";

import { isEmbedId, isEmbedProvider } from "@/lib/lesson-embed";
import { safeLessonLink } from "@/lib/lesson-markup";
import {
  createDomainLessonMaterial,
  listDomainLessonMaterials
} from "@/lib/internal-api-client.server";

import {
  authenticatedSession,
  canonicalUuid,
  mappedError,
  notFound,
  safeVersion,
  unauthorized
} from "../../../../../../../_route-shared";
import {
  curriculumActions,
  curriculumCsrf,
  curriculumInvalidInput,
  lessonRouteIds,
  prepareCurriculumMutation,
  validCurriculumTitle,
  type LessonRouteContext
} from "../../../../../_curriculum-route-shared";

const CREATE_FIELDS = [
  "_csrf",
  "idempotency_key",
  "expected_course_version",
  "kind",
  "title",
  "url",
  "provider",
  "video_id"
] as const;


export async function GET(request: NextRequest, context: LessonRouteContext) {
  const ids = await lessonRouteIds(context);
  if (!ids) return notFound();
  let authenticated: Awaited<ReturnType<typeof authenticatedSession>>;
  try {
    authenticated = await authenticatedSession(request);
  } catch (error) {
    return mappedError(error);
  }
  if (!authenticated) return unauthorized();
  try {
    const payload = await listDomainLessonMaterials(
      authenticated.sessionId,
      authenticated.session,
      ids.sellerId,
      ids.productId,
      ids.courseId,
      ids.moduleId,
      ids.lessonId
    );
    return NextResponse.json(
      { ...(payload as object), csrf: curriculumCsrf(authenticated.sessionId) },
      { headers: { "cache-control": "no-store" } }
    );
  } catch (error) {
    return mappedError(error);
  }
}

export async function POST(request: NextRequest, context: LessonRouteContext) {
  const ids = await lessonRouteIds(context);
  if (!ids) return notFound();
  const prepared = await prepareCurriculumMutation(request, curriculumActions.create, CREATE_FIELDS);
  if (!prepared.ok) return prepared.response;

  const idempotencyKey = canonicalUuid(prepared.fields.idempotency_key);
  const expectedCourseVersion = safeVersion(prepared.fields.expected_course_version);
  if (!idempotencyKey) return curriculumInvalidInput("idempotency_key");
  if (expectedCourseVersion === null) return curriculumInvalidInput("expected_course_version");

  const kind = prepared.fields.kind;
  if (kind !== "external" && kind !== "embed") return curriculumInvalidInput("kind");
  const title = prepared.fields.title;
  if (!validCurriculumTitle(title)) return curriculumInvalidInput("title");

  // A material carries exactly the fields its own kind needs. Accepting the
  // other kind's fields as well would let a row describe two things at once,
  // and the database refuses that shape anyway.
  if (kind === "external") {
    const url = safeLessonLink(prepared.fields.url ?? "");
    if (!url) return curriculumInvalidInput("url");
    if (prepared.fields.provider || prepared.fields.video_id) {
      return curriculumInvalidInput("kind");
    }
    return await send(prepared, ids, {
      idempotencyKey,
      expectedCourseVersion,
      kind,
      title,
      url,
      provider: null,
      videoId: null
    });
  }

  const provider = prepared.fields.provider;
  const videoId = prepared.fields.video_id;
  if (!isEmbedProvider(provider)) return curriculumInvalidInput("provider");
  if (!isEmbedId(videoId)) return curriculumInvalidInput("video_id");
  if (prepared.fields.url) return curriculumInvalidInput("kind");
  return await send(prepared, ids, {
    idempotencyKey,
    expectedCourseVersion,
    kind,
    title,
    url: null,
    provider,
    videoId
  });
}

type PreparedMutation = Extract<
  Awaited<ReturnType<typeof prepareCurriculumMutation>>,
  { ok: true }
>;
type LessonIds = NonNullable<Awaited<ReturnType<typeof lessonRouteIds>>>;

async function send(
  prepared: PreparedMutation,
  ids: LessonIds,
  input: Parameters<typeof createDomainLessonMaterial>[7]
) {
  try {
    const payload = await createDomainLessonMaterial(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      ids.sellerId,
      ids.productId,
      ids.courseId,
      ids.moduleId,
      ids.lessonId,
      input
    );
    return NextResponse.json(payload, {
      status: 201,
      headers: { "cache-control": "no-store" }
    });
  } catch (error) {
    return mappedError(error);
  }
}
