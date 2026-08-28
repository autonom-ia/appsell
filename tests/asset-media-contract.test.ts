import { describe, expect, it } from "vitest";

import { MAX_PDF_BYTES, MAX_VIDEO_BYTES, validAssetMediaFile , ASSET_UPLOAD_ACCEPT, PDF_MEDIA_TYPE, SUPPORTED_ASSET_MEDIA, VIDEO_MEDIA_TYPE, acceptForContentRole, acceptedByBothPanels, assetMediaSpec, assetMediaTypeForFile, isDocumentMedia, isDownloadableMedia } from "../lib/asset-media";

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

describe("os dois painéis do editor de conteúdo", () => {
  /**
   * O painel em que o vendedor clica é o que decide o papel do material. Se um
   * painel oferecer um formato que o papel dele não comporta, o servidor recusa
   * a política depois — e o vendedor descobre tarde.
   */
  it("Conteúdo da aula só oferece o que o player sabe apresentar", () => {
    const accept = acceptForContentRole("course_content");
    expect(accept).toContain(".pdf");
    expect(accept).toContain(".mp4");
    for (const extensao of [".docx", ".xlsx", ".pptx", ".txt", ".md", ".csv", ".json"]) {
      expect(accept).not.toContain(extensao);
    }
  });

  it("Anexos e links não oferece vídeo, que o aluno não pode baixar", () => {
    const accept = acceptForContentRole("support_material");
    // O MP4 tem `downloadable: false` e `set_lesson_asset_policy` recusa
    // download em vídeo. Oferecê-lo aqui deixaria o vendedor substituir um
    // documento por um vídeo e cair numa política impossível.
    expect(accept).not.toContain(".mp4");
    expect(accept).not.toContain(VIDEO_MEDIA_TYPE);
    for (const extensao of [".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".csv", ".json"]) {
      expect(accept).toContain(extensao);
    }
  });

  it("o PDF é o único formato que os dois painéis aceitam", () => {
    const dosDois = Object.keys(SUPPORTED_ASSET_MEDIA).filter(acceptedByBothPanels);
    expect(dosDois).toEqual([PDF_MEDIA_TYPE]);
  });
});
