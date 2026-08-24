import { describe, expect, it } from "vitest";

import { pdfStudyEnabled } from "@/lib/pdf-study-config";
import { parsePdfStudyState, validPdfStudyMutation } from "@/lib/pdf-study";

const ASSET_ID = "85000000-0000-4000-8000-000000000001";
const ANNOTATION_ID = "86000000-0000-4000-8000-000000000001";

function state() {
  return {
    asset_id: ASSET_ID,
    asset_version: 2,
    page_count: 65,
    revision: 4,
    annotations: [{
      id: ANNOTATION_ID,
      page: 35,
      tool: "highlight",
      color: "#E59819",
      width: 0.022,
      points: [{ x: 0.1, y: 0.2 }, { x: 0.8, y: 0.2 }]
    }],
    bookmarks: [1, 35],
    updated_at: "2026-08-24T12:00:00Z"
  };
}

describe("PDF study contract", () => {
  it("accepts only the current asset, page count and normalized vector data", () => {
    expect(parsePdfStudyState(state(), ASSET_ID, 65)).toEqual(state());
    expect(parsePdfStudyState({ ...state(), asset_version: 0 }, ASSET_ID, 65)).toBeNull();
    expect(parsePdfStudyState({ ...state(), asset_id: crypto.randomUUID() }, ASSET_ID, 65)).toBeNull();
    expect(parsePdfStudyState({ ...state(), bookmarks: [66] }, ASSET_ID, 65)).toBeNull();
    expect(parsePdfStudyState({
      ...state(),
      annotations: [{ ...state().annotations[0], points: [{ x: -0.1, y: 0.2 }] }]
    }, ASSET_ID, 65)).toBeNull();
  });

  it("accepts only bounded annotation and bookmark mutations", () => {
    expect(validPdfStudyMutation({
      action: "annotation.upsert",
      annotation: state().annotations[0]
    }, 65)).toBe(true);
    expect(validPdfStudyMutation({
      action: "annotation.delete",
      id: ANNOTATION_ID
    }, 65)).toBe(true);
    expect(validPdfStudyMutation({ action: "bookmark.set", page: 35, active: true }, 65)).toBe(true);
    expect(validPdfStudyMutation({ action: "bookmark.set", page: 66, active: true }, 65)).toBe(false);
    expect(validPdfStudyMutation({ action: "annotation.delete", id: ANNOTATION_ID, leak: true }, 65))
      .toBe(false);
  });

  it("supports a data-preserving rollback switch", () => {
    expect(pdfStudyEnabled({})).toBe(true);
    expect(pdfStudyEnabled({ APPSELL_PDF_STUDY_ENABLED: "true" })).toBe(true);
    expect(pdfStudyEnabled({ APPSELL_PDF_STUDY_ENABLED: "false" })).toBe(false);
    expect(pdfStudyEnabled({ APPSELL_PDF_STUDY_ENABLED: "invalid" })).toBe(false);
  });
});
