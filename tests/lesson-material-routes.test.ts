import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

const {
  createDomainLessonMaterial,
  listDomainLessonMaterials,
  archiveDomainLessonMaterial,
  getSession,
  consumeRateLimit,
  verifyMutation,
  DomainApiError
} = vi.hoisted(() => {
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return {
    createDomainLessonMaterial: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
    listDomainLessonMaterials: vi.fn(async () => ({ materials: [] })),
    archiveDomainLessonMaterial: vi.fn(async () => ({ changed: true })),
    getSession: vi.fn(async () => ({ identity: { sub: "seller" } })),
    consumeRateLimit: vi.fn(async () => true),
    verifyMutation: vi.fn(() => true),
    DomainApiError
  };
});

vi.mock("@/lib/internal-api-client.server", () => ({
  createDomainLessonMaterial,
  listDomainLessonMaterials,
  archiveDomainLessonMaterial,
  DomainApiError
}));

vi.mock("@/lib/oauth-service.server", () => ({
  oauthService: () => ({ getSession, consumeRateLimit })
}));

vi.mock("@/lib/redis-oauth-service", () => ({
  isOAuthStoreUnavailable: () => false
}));

vi.mock("@/lib/mutation-protection.server", () => ({
  CREATE_CURRICULUM_CSRF_ACTION: "POST:/api/curriculum/create",
  UPDATE_CURRICULUM_CSRF_ACTION: "POST:/api/curriculum/update",
  REORDER_CURRICULUM_CSRF_ACTION: "POST:/api/curriculum/reorder",
  ARCHIVE_CURRICULUM_CSRF_ACTION: "POST:/api/curriculum/archive",
  csrfTokenForSession: () => "csrf-token-test",
  verifyMutation
}));

import { POST as createMaterial } from
  "../app/api/sellers/[sellerId]/products/[productId]/courses/[courseId]/modules/[moduleId]/lessons/[lessonId]/materials/route";

const ORIGIN = "http://localhost:3000";
const SELLER_ID = "44444444-4444-4444-8444-444444444444";
const PRODUCT_ID = "55555555-5555-4555-8555-555555555555";
const COURSE_ID = "66666666-6666-4666-8666-666666666666";
const MODULE_ID = "77777777-7777-4777-8777-777777777777";
const LESSON_ID = "88888888-8888-4888-8888-888888888888";
const IDEMPOTENCY = "99999999-9999-4999-8999-999999999999";

const lessonContext = {
  params: Promise.resolve({
    sellerId: SELLER_ID,
    productId: PRODUCT_ID,
    courseId: COURSE_ID,
    moduleId: MODULE_ID,
    lessonId: LESSON_ID
  })
};

const originalEnvironment = { ...process.env };
const PATH = `/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}/courses/${COURSE_ID}`
  + `/modules/${MODULE_ID}/lessons/${LESSON_ID}/materials`;

function baseFields(overrides: Record<string, string> = {}) {
  return {
    _csrf: "csrf-token-test",
    idempotency_key: IDEMPOTENCY,
    expected_course_version: "3",
    kind: "external",
    title: "Guia de estudo",
    url: "https://exemplo.com/guia",
    provider: "",
    video_id: "",
    ...overrides
  };
}

function formRequest(entries: Record<string, string>) {
  const form = new FormData();
  for (const [key, value] of Object.entries(entries)) form.set(key, value);
  return new NextRequest(`${ORIGIN}${PATH}`, {
    method: "POST",
    headers: { cookie: "appsell_session=session-1", origin: ORIGIN },
    body: form
  });
}

describe("lesson material routes", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createDomainLessonMaterial.mockResolvedValue({ material_id: "material-1", course_version: 4, position: 1 });
    verifyMutation.mockReturnValue(true);
    getSession.mockResolvedValue({ identity: { sub: "seller" } });
    consumeRateLimit.mockResolvedValue(true);
    process.env.APPSELL_PUBLIC_ORIGIN = ORIGIN;
  });

  afterEach(() => {
    process.env = { ...originalEnvironment };
  });

  it("forwards an https external link", async () => {
    const response = await createMaterial(formRequest(baseFields()), lessonContext);

    expect(response.status).toBe(201);
    expect(createDomainLessonMaterial).toHaveBeenCalledTimes(1);
    const input = createDomainLessonMaterial.mock.calls[0]?.[7] as { url: string; kind: string };
    expect(input.kind).toBe("external");
    expect(input.url).toBe("https://exemplo.com/guia");
  });

  it.each([
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "http://exemplo.com/guia",
    "file:///etc/passwd",
    "https://exemplo.com/\" onmouseover=\"alert(1)",
    "https://exemplo.com/ espaco"
  ])("refuses %s and never reaches the domain", async (url) => {
    const response = await createMaterial(formRequest(baseFields({ url })), lessonContext);

    expect(response.status).toBe(422);
    expect(await response.json()).toMatchObject({ field: "url" });
    expect(createDomainLessonMaterial).not.toHaveBeenCalled();
  });

  it("forwards an embed built from a provider and an identifier", async () => {
    const response = await createMaterial(
      formRequest(baseFields({ kind: "embed", url: "", provider: "youtube", video_id: "dQw4w9WgXcQ" })),
      lessonContext
    );

    expect(response.status).toBe(201);
    const input = createDomainLessonMaterial.mock.calls[0]?.[7] as {
      provider: string; videoId: string; url: string | null;
    };
    expect(input.provider).toBe("youtube");
    expect(input.videoId).toBe("dQw4w9WgXcQ");
    expect(input.url).toBeNull();
  });

  it.each([
    "https://youtube.com/watch?v=abc",
    "abc/../../etc",
    "abc def",
    "<script>",
    "a".repeat(65)
  ])("refuses %s as an embed identifier", async (video_id) => {
    const response = await createMaterial(
      formRequest(baseFields({ kind: "embed", url: "", provider: "youtube", video_id })),
      lessonContext
    );

    expect(response.status).toBe(422);
    expect(await response.json()).toMatchObject({ field: "video_id" });
    expect(createDomainLessonMaterial).not.toHaveBeenCalled();
  });

  it("refuses an unknown provider", async () => {
    const response = await createMaterial(
      formRequest(baseFields({ kind: "embed", url: "", provider: "tiktok", video_id: "abc" })),
      lessonContext
    );

    expect(response.status).toBe(422);
    expect(await response.json()).toMatchObject({ field: "provider" });
    expect(createDomainLessonMaterial).not.toHaveBeenCalled();
  });

  it("refuses a row that describes both kinds at once", async () => {
    // The database refuses this shape too. Catching it here keeps a confused
    // request from spending an idempotency key on a row that cannot exist.
    const both = await createMaterial(
      formRequest(baseFields({ kind: "external", provider: "youtube", video_id: "abc" })),
      lessonContext
    );
    expect(both.status).toBe(422);

    const embedWithUrl = await createMaterial(
      formRequest(baseFields({ kind: "embed", provider: "youtube", video_id: "abc", url: "https://exemplo.com" })),
      lessonContext
    );
    expect(embedWithUrl.status).toBe(422);
    expect(createDomainLessonMaterial).not.toHaveBeenCalled();
  });

  it("refuses a request whose CSRF token does not verify", async () => {
    verifyMutation.mockReturnValue(false);

    const response = await createMaterial(formRequest(baseFields()), lessonContext);

    expect(response.status).toBe(403);
    expect(createDomainLessonMaterial).not.toHaveBeenCalled();
  });

  it("refuses a title outside the accepted length", async () => {
    const short = await createMaterial(formRequest(baseFields({ title: "a" })), lessonContext);
    expect(short.status).toBe(422);

    const long = await createMaterial(formRequest(baseFields({ title: "a".repeat(161) })), lessonContext);
    expect(long.status).toBe(422);
    expect(createDomainLessonMaterial).not.toHaveBeenCalled();
  });
});
