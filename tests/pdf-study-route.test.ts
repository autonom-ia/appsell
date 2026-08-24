import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

const {
  csrfTokenForSession,
  getSession,
  mutateDomainLearningPdfStudy,
  readDomainLearningPdfStudy,
  verifyMutation,
  DomainApiError
} = vi.hoisted(() => {
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return {
    csrfTokenForSession: vi.fn(() => "pdf-study-csrf-token-for-tests"),
    getSession: vi.fn(),
    mutateDomainLearningPdfStudy: vi.fn(),
    readDomainLearningPdfStudy: vi.fn(),
    verifyMutation: vi.fn(() => true),
    DomainApiError
  };
});

vi.mock("@/lib/internal-api-client.server", () => ({
  mutateDomainLearningPdfStudy,
  readDomainLearningPdfStudy,
  DomainApiError
}));

vi.mock("@/lib/oauth-service.server", () => ({
  oauthService: () => ({ getSession })
}));

vi.mock("@/lib/redis-oauth-service", () => ({
  isOAuthStoreUnavailable: () => false
}));

vi.mock("@/lib/mutation-protection.server", () => ({
  COMPLETE_LEARNING_LESSON_CSRF_ACTION: "POST:/api/library/complete",
  MUTATE_PDF_STUDY_CSRF_ACTION: "POST:/api/library/pdf-study",
  UPDATE_LEARNING_PROGRESS_CSRF_ACTION: "PUT:/api/library/progress",
  csrfTokenForSession,
  verifyMutation
}));

import { GET, POST } from "../app/api/library/items/[entitlementId]/assets/[assetId]/study/route";

const ORIGIN = "http://localhost:3000";
const ENTITLEMENT_ID = "bee105e2-7809-5bf5-874a-d6332dc4a4ef";
const ASSET_ID = "a6f01626-8b83-55e5-becc-fbab785f1493";
const ANNOTATION_ID = "86000000-0000-4000-8000-000000000001";
const IDEMPOTENCY_KEY = "87000000-0000-4000-8000-000000000001";
const context = { params: Promise.resolve({ entitlementId: ENTITLEMENT_ID, assetId: ASSET_ID }) };
const originalEnvironment = { ...process.env };

function state(revision = 0) {
  return {
    asset_id: ASSET_ID,
    asset_version: 2,
    page_count: 65,
    revision,
    annotations: [],
    bookmarks: [],
    updated_at: revision === 0 ? null : "2026-08-24T12:00:00Z"
  };
}

function getRequest(cookie = true) {
  return new NextRequest(`${ORIGIN}/api/library/items/${ENTITLEMENT_ID}/assets/${ASSET_ID}/study`, {
    headers: cookie ? { cookie: "appsell_session=session-1" } : undefined
  });
}

function postRequest(body: unknown = validBody(), cookie = true) {
  return new NextRequest(`${ORIGIN}/api/library/items/${ENTITLEMENT_ID}/assets/${ASSET_ID}/study`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      origin: ORIGIN,
      ...(cookie ? { cookie: "appsell_session=session-1" } : {})
    },
    body: JSON.stringify(body)
  });
}

function validBody() {
  return {
    _csrf: "pdf-study-csrf-token-for-tests",
    idempotency_key: IDEMPOTENCY_KEY,
    expected_revision: 0,
    mutation: {
      action: "annotation.upsert",
      annotation: {
        id: ANNOTATION_ID,
        page: 1,
        tool: "pen",
        color: "#173B73",
        width: 0.003,
        points: [{ x: 0.1, y: 0.2 }, { x: 0.6, y: 0.4 }]
      }
    }
  };
}

describe("protected PDF study BFF", () => {
  beforeEach(() => {
    process.env.APPSELL_PDF_STUDY_ENABLED = "true";
    getSession.mockReset();
    getSession.mockResolvedValue({ subject: "buyer-1" });
    readDomainLearningPdfStudy.mockReset();
    readDomainLearningPdfStudy.mockResolvedValue({ state: state() });
    mutateDomainLearningPdfStudy.mockReset();
    mutateDomainLearningPdfStudy.mockResolvedValue({ state: state(1) });
    verifyMutation.mockReset();
    verifyMutation.mockReturnValue(true);
  });

  afterEach(() => {
    process.env = { ...originalEnvironment };
  });

  it("returns only the private study state, CSRF token and no-store policy", async () => {
    const response = await GET(getRequest(), context);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      state: state(),
      csrf: "pdf-study-csrf-token-for-tests"
    });
    expect(response.headers.get("cache-control")).toContain("no-store");
    expect(response.headers.get("vary")).toBe("Cookie");
    expect(readDomainLearningPdfStudy).toHaveBeenCalledWith(
      "session-1", expect.anything(), ENTITLEMENT_ID, ASSET_ID
    );
  });

  it("writes a strictly validated mutation after session and CSRF checks", async () => {
    const response = await POST(postRequest(), context);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ state: state(1) });
    expect(verifyMutation).toHaveBeenCalledWith(
      expect.anything(), "session-1", "POST:/api/library/pdf-study",
      "pdf-study-csrf-token-for-tests"
    );
    expect(mutateDomainLearningPdfStudy).toHaveBeenCalledWith(
      "session-1",
      expect.anything(),
      ENTITLEMENT_ID,
      ASSET_ID,
      expect.objectContaining({
        idempotency_key: IDEMPOTENCY_KEY,
        expected_revision: 0,
        mutation: validBody().mutation
      })
    );
  });

  it("fails closed for anonymous, forbidden and invalid requests without disclosing data", async () => {
    expect((await GET(getRequest(false), context)).status).toBe(401);
    readDomainLearningPdfStudy.mockRejectedValueOnce(new DomainApiError(403, "forbidden"));
    expect((await GET(getRequest(), context)).status).toBe(404);
    verifyMutation.mockReturnValue(false);
    expect((await POST(postRequest(), context)).status).toBe(403);
    expect(mutateDomainLearningPdfStudy).not.toHaveBeenCalled();
    verifyMutation.mockReturnValue(true);
    expect((await POST(postRequest({ ...validBody(), unexpected: true }), context)).status).toBe(422);
    expect(mutateDomainLearningPdfStudy).not.toHaveBeenCalled();
  });

  it("rejects oversized JSON before calling the domain API", async () => {
    const response = await POST(postRequest({ ...validBody(), padding: "x".repeat(70_000) }), context);
    expect(response.status).toBe(413);
    expect(mutateDomainLearningPdfStudy).not.toHaveBeenCalled();
  });

  it("disables read and write through the rollback flag without touching stored state", async () => {
    process.env.APPSELL_PDF_STUDY_ENABLED = "false";
    expect((await GET(getRequest(), context)).status).toBe(404);
    expect((await POST(postRequest(), context)).status).toBe(404);
    expect(readDomainLearningPdfStudy).not.toHaveBeenCalled();
    expect(mutateDomainLearningPdfStudy).not.toHaveBeenCalled();
  });
});
