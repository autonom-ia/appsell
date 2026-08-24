import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  parseDomainPdfManifest,
  publicPdfManifest,
  safeVideoRange,
  trustedPdfVideoTarget
} from "../lib/protected-pdf-links.server";

const ASSET_ID = "a6f01626-8b83-55e5-becc-fbab785f1493";
const BASE_HREF = `/api/library/items/bee105e2-7809-5bf5-874a-d6332dc4a4ef/assets/${ASSET_ID}/pages`;
const MANIFEST = {
  asset: {
    id: ASSET_ID,
    page_count: 6,
    links: [
      {
        id: "0123456789abcdef",
        page: 1,
        kind: "page",
        target_page: 4,
        rect: { left: 8, top: 12, width: 70, height: 7 }
      },
      {
        id: "fedcba9876543210",
        page: 4,
        kind: "video",
        target_url: "https://media.example.test/course/lesson.mov",
        rect: { left: 12, top: 28, width: 76, height: 26 }
      }
    ]
  }
};

afterEach(() => vi.unstubAllEnvs());

describe("protected PDF links", () => {
  it("keeps raw video targets server-side and publishes a same-origin media route", () => {
    const parsed = parseDomainPdfManifest(MANIFEST, ASSET_ID);
    expect(parsed).not.toBeNull();
    const publicManifest = publicPdfManifest(parsed!, BASE_HREF);
    expect(JSON.stringify(publicManifest)).not.toContain("media.example.test");
    expect(publicManifest.links[1]).toMatchObject({
      kind: "video",
      media_href: `${BASE_HREF}/links/fedcba9876543210/media`
    });
  });

  it("rejects malformed links and targets outside the exact HTTPS allowlist", () => {
    expect(parseDomainPdfManifest({
      asset: { ...MANIFEST.asset, links: [{ ...MANIFEST.asset.links[0], target_page: 7 }] }
    }, ASSET_ID)).toBeNull();
    vi.stubEnv("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", "https://media.example.test");
    expect(trustedPdfVideoTarget("https://media.example.test/course/lesson.mov")?.href)
      .toBe("https://media.example.test/course/lesson.mov");
    expect(trustedPdfVideoTarget("https://media.example.test.evil.test/course/lesson.mov"))
      .toBeNull();
    expect(trustedPdfVideoTarget("https://media.example.test/course/lesson.pdf"))
      .toBeNull();
    expect(trustedPdfVideoTarget("http://media.example.test/course/lesson.mov"))
      .toBeNull();
  });

  it("accepts one bounded byte range and rejects multi-range requests", () => {
    expect(safeVideoRange(null)).toBeNull();
    expect(safeVideoRange("bytes=0-1023")).toBe("bytes=0-1023");
    expect(safeVideoRange("bytes=-1024")).toBe("bytes=-1024");
    expect(safeVideoRange("bytes=0-1,4-5")).toBeUndefined();
  });
});
