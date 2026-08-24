import { NextRequest, NextResponse } from "next/server";

import {
  mutateDomainLearningPdfStudy,
  readDomainLearningPdfStudy
} from "@/lib/internal-api-client.server";
import { JsonPayloadTooLargeError, readLimitedJson } from "@/lib/limited-json.server";
import { MUTATE_PDF_STUDY_CSRF_ACTION, csrfTokenForSession, verifyMutation } from "@/lib/mutation-protection.server";
import { pdfStudyEnabled } from "@/lib/pdf-study-config";
import { parsePdfStudyEnvelope, validPdfStudyMutation } from "@/lib/pdf-study";
import {
  authenticatedLearningSession,
  learningAssetIds,
  learningError,
  learningForbiddenMutation,
  learningJsonErrorForInvalidInput,
  learningNotFound,
  learningUnauthorized,
  type LearningAssetRouteContext
} from "../../../../_route-shared";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const MAX_BODY_BYTES = 65_536;

export async function GET(request: NextRequest, context: LearningAssetRouteContext) {
  if (!pdfStudyEnabled()) return learningNotFound();
  try {
    const authenticated = await authenticatedLearningSession(request);
    if (!authenticated) return learningUnauthorized();
    const ids = await learningAssetIds(context);
    if (!ids) return learningNotFound();
    const payload = await readDomainLearningPdfStudy(
      authenticated.sessionId, authenticated.session, ids.entitlementId, ids.assetId
    );
    const state = parsePdfStudyEnvelope(payload, ids.assetId);
    if (!state) return unavailable();
    return NextResponse.json({
      state,
      csrf: csrfTokenForSession(authenticated.sessionId, MUTATE_PDF_STUDY_CSRF_ACTION)
    }, { headers: noStoreHeaders() });
  } catch (error) {
    return learningError(error);
  }
}

export async function POST(request: NextRequest, context: LearningAssetRouteContext) {
  if (!pdfStudyEnabled()) return learningNotFound();
  try {
    const authenticated = await authenticatedLearningSession(request);
    if (!authenticated) return learningUnauthorized();
    const ids = await learningAssetIds(context);
    if (!ids) return learningNotFound();
    const body = await readLimitedJson(request, MAX_BODY_BYTES);
    if (!validRequest(body)) return learningJsonErrorForInvalidInput();
    if (!verifyMutation(
      request, authenticated.sessionId, MUTATE_PDF_STUDY_CSRF_ACTION, body._csrf
    )) return learningForbiddenMutation();
    const payload = await mutateDomainLearningPdfStudy(
      authenticated.sessionId,
      authenticated.session,
      ids.entitlementId,
      ids.assetId,
      {
        idempotency_key: body.idempotency_key,
        expected_revision: body.expected_revision,
        mutation: body.mutation
      }
    );
    const state = parsePdfStudyEnvelope(payload, ids.assetId);
    return state
      ? NextResponse.json({ state }, { headers: noStoreHeaders() })
      : unavailable();
  } catch (error) {
    if (error instanceof JsonPayloadTooLargeError) {
      return NextResponse.json({ error: "payload_too_large" }, {
        status: 413, headers: noStoreHeaders()
      });
    }
    return learningError(error);
  }
}

function validRequest(value: unknown): value is {
  _csrf: string;
  idempotency_key: string;
  expected_revision: number;
  mutation: Record<string, unknown>;
} {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const body = value as Record<string, unknown>;
  const keys = Object.keys(body).sort().join(",");
  return keys === "_csrf,expected_revision,idempotency_key,mutation" &&
    typeof body._csrf === "string" && typeof body.idempotency_key === "string" &&
    UUID.test(body.idempotency_key) && Number.isSafeInteger(body.expected_revision) &&
    Number(body.expected_revision) >= 0 && validPdfStudyMutation(body.mutation);
}

function unavailable() {
  return NextResponse.json({ error: "pdf_study_unavailable" }, {
    status: 503, headers: noStoreHeaders()
  });
}

function noStoreHeaders() {
  return {
    "cache-control": "private, no-store, max-age=0",
    pragma: "no-cache",
    "surrogate-control": "no-store",
    vary: "Cookie",
    "x-content-type-options": "nosniff"
  };
}
