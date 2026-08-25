import { describe, expect, it } from "vitest";

import { MAX_PDF_BYTES, MAX_VIDEO_BYTES, validAssetMediaFile , ASSET_UPLOAD_ACCEPT, assetMediaSpec, assetMediaTypeForFile, isDocumentMedia, isDownloadableMedia } from "../lib/asset-media";

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

describe("document support material", () => {
  const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  const PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation";
  const DOCM = "application/vnd.ms-word.document.macroEnabled.12";

  it("accepts each document format at its own ceiling and refuses one byte past it", () => {
    expect(validAssetMediaFile("roteiro.docx", DOCX, 20 * 1024 * 1024)).toBe(true);
    expect(validAssetMediaFile("roteiro.docx", DOCX, 20 * 1024 * 1024 + 1)).toBe(false);
    expect(validAssetMediaFile("aula.pptx", PPTX, 40 * 1024 * 1024)).toBe(true);
    expect(validAssetMediaFile("aula.pptx", PPTX, 40 * 1024 * 1024 + 1)).toBe(false);
    expect(validAssetMediaFile("notas.txt", "text/plain", 2 * 1024 * 1024)).toBe(true);
    expect(validAssetMediaFile("notas.txt", "text/plain", 2 * 1024 * 1024 + 1)).toBe(false);
  });

  it("never accepts a macro bearing office format", () => {
    // The browser refuses early for clarity; the server refuses by opening the
    // container. Neither may ever admit this type.
    expect(validAssetMediaFile("macro.docm", DOCM, 1_000)).toBe(false);
    expect(assetMediaSpec(DOCM)).toBeNull();
    expect(assetMediaTypeForFile("macro.docm", DOCM)).toBeNull();
  });

  it("refuses a file whose extension disagrees with its declared type", () => {
    expect(validAssetMediaFile("planilha.docx", XLSX, 1_000)).toBe(false);
    expect(assetMediaTypeForFile("roteiro.docx", "video/mp4")).toBeNull();
  });

  it("resolves the media type of a picked file, tolerating an empty browser type", () => {
    // Office and text files frequently arrive with an empty type from the OS.
    expect(assetMediaTypeForFile("roteiro.docx", "")).toBe(DOCX);
    expect(assetMediaTypeForFile("dados.json", "application/json")).toBe("application/json");
    expect(assetMediaTypeForFile("imagem.gif", "image/gif")).toBeNull();
  });

  it("keeps documents downloadable and video not", () => {
    expect(isDocumentMedia(DOCX)).toBe(true);
    expect(isDocumentMedia("application/pdf")).toBe(false);
    expect(isDownloadableMedia(DOCX)).toBe(true);
    expect(isDownloadableMedia("video/mp4")).toBe(false);
  });

  it("offers every accepted format to the file picker", () => {
    expect(ASSET_UPLOAD_ACCEPT).toContain(".docx");
    expect(ASSET_UPLOAD_ACCEPT).toContain(".csv");
    expect(ASSET_UPLOAD_ACCEPT).toContain(".mp4");
    expect(ASSET_UPLOAD_ACCEPT).not.toContain(".docm");
  });
});
