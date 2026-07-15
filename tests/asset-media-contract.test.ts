import { describe, expect, it } from "vitest";

import { MAX_PDF_BYTES, MAX_VIDEO_BYTES, validAssetMediaFile } from "../lib/asset-media";

describe("private media upload contract", () => {
  it("accepts MP4 at the bounded video limit", () => {
    expect(validAssetMediaFile("aula-segura.mp4", "video/mp4", MAX_VIDEO_BYTES)).toBe(true);
  });

  it("fails closed on MIME/extension mismatch and per-media overflow", () => {
    expect(validAssetMediaFile("aula-segura.pdf", "video/mp4", 10)).toBe(false);
    expect(validAssetMediaFile("aula-segura.mp4", "video/mp4", MAX_VIDEO_BYTES + 1)).toBe(false);
    expect(validAssetMediaFile("material.pdf", "application/pdf", MAX_PDF_BYTES + 1)).toBe(false);
  });

  it("preserves the existing PDF contract", () => {
    expect(validAssetMediaFile("material.pdf", "application/pdf", MAX_PDF_BYTES)).toBe(true);
  });
});
