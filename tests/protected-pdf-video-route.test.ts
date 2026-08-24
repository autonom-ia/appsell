import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

const { getSession, readDomainLearningPdfManifest, DomainApiError } = vi.hoisted(() => {
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return {
    getSession: vi.fn(),
    readDomainLearningPdfManifest: vi.fn(),
    DomainApiError
  };
});

vi.mock("@/lib/internal-api-client.server", () => ({
  readDomainLearningPdfManifest,
  DomainApiError
}));

vi.mock("@/lib/oauth-service.server", () => ({
  oauthService: () => ({ getSession })
}));

import { GET } from "../app/api/library/items/[entitlementId]/assets/[assetId]/pages/links/[linkId]/media/route";

const ORIGIN = "http://localhost:3000";
const ENTITLEMENT_ID = "bee105e2-7809-5bf5-874a-d6332dc4a4ef";
const ASSET_ID = "a6f01626-8b83-55e5-becc-fbab785f1493";
const LINK_ID = "fedcba9876543210";
const VIDEO_URL = "https://media.example.test/course/lesson.mov";
const context = {
  params: Promise.resolve({ entitlementId: ENTITLEMENT_ID, assetId: ASSET_ID, linkId: LINK_ID })
};

const manifest = {
  asset: {
    id: ASSET_ID,
    page_count: 6,
    links: [{
      id: LINK_ID,
      page: 4,
      kind: "video",
      target_url: VIDEO_URL,
      rect: { left: 12, top: 28, width: 76, height: 26 }
    }]
  }
};

function request({ cookie = true, range = "bytes=0-3" } = {}) {
  const headers: Record<string, string> = {};
  if (cookie) headers.cookie = "appsell_session=session-1";
  if (range) headers.range = range;
  return new NextRequest(
    `${ORIGIN}/api/library/items/${ENTITLEMENT_ID}/assets/${ASSET_ID}/pages/links/${LINK_ID}/media`,
    { headers }
  );
}

describe("protected PDF video proxy", () => {
  beforeEach(() => {
    vi.stubEnv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", "https://media.example.test");
    getSession.mockReset();
    getSession.mockResolvedValue({ subject: "buyer-1" });
    readDomainLearningPdfManifest.mockReset();
    readDomainLearningPdfManifest.mockResolvedValue(manifest);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("streams only the authorized allowlisted target through the same-origin route", async () => {
    const upstreamFetch = vi.fn(async () => new Response(new Uint8Array([1, 2, 3, 4]), {
      status: 206,
      headers: {
        "content-type": "video/quicktime",
        "content-length": "4",
        "content-range": "bytes 0-3/4"
      }
    }));
    vi.stubGlobal("fetch", upstreamFetch);

    const response = await GET(request(), context);

    expect(response.status).toBe(206);
    expect(response.headers.get("content-disposition")).toBe(
      'inline; filename="video-protegido.mov"'
    );
    expect(response.headers.get("cache-control")).toContain("no-store");
    expect(Array.from(new Uint8Array(await response.arrayBuffer()))).toEqual([1, 2, 3, 4]);
    expect(upstreamFetch).toHaveBeenCalledWith(new URL(VIDEO_URL), expect.objectContaining({
      redirect: "error",
      cache: "no-store",
      headers: { range: "bytes=0-3" }
    }));
    expect(readDomainLearningPdfManifest).toHaveBeenCalledWith(
      "session-1",
      expect.anything(),
      ENTITLEMENT_ID,
      ASSET_ID
    );
  });

  it("denies anonymous, multi-range and non-allowlisted requests before fetching media", async () => {
    const upstreamFetch = vi.fn();
    vi.stubGlobal("fetch", upstreamFetch);

    expect((await GET(request({ cookie: false }), context)).status).toBe(401);
    expect((await GET(request({ range: "bytes=0-1,4-5" }), context)).status).toBe(416);
    vi.stubEnv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", "https://other.example.test");
    expect((await GET(request(), context)).status).toBe(503);
    expect(upstreamFetch).not.toHaveBeenCalled();
  });
});
