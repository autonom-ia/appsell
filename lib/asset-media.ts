/**
 * The accepted asset media, mirrored from the server side catalogue.
 *
 * Documents are support material and are always downloaded, never opened inside
 * the page, so nothing in the player has to decide how to display a spreadsheet.
 * The macro bearing office formats are absent on purpose: an extension is a
 * claim, and the only place that claim is tested is the server, which opens the
 * container. This table exists so the browser refuses early and clearly, not so
 * it can be trusted.
 */
export interface AssetMediaSpec {
  extension: string;
  maxBytes: number;
  supportMaterialOnly: boolean;
  downloadable: boolean;
}

const MIB = 1024 * 1024;

export const PDF_MEDIA_TYPE = "application/pdf";
export const VIDEO_MEDIA_TYPE = "video/mp4";
export const DOCX_MEDIA_TYPE =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
export const XLSX_MEDIA_TYPE =
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
export const PPTX_MEDIA_TYPE =
  "application/vnd.openxmlformats-officedocument.presentationml.presentation";

export const SUPPORTED_ASSET_MEDIA: Readonly<Record<string, AssetMediaSpec>> = {
  [PDF_MEDIA_TYPE]: { extension: "pdf", maxBytes: 100 * MIB, supportMaterialOnly: false, downloadable: true },
  [VIDEO_MEDIA_TYPE]: { extension: "mp4", maxBytes: 100 * MIB, supportMaterialOnly: false, downloadable: false },
  [DOCX_MEDIA_TYPE]: { extension: "docx", maxBytes: 20 * MIB, supportMaterialOnly: true, downloadable: true },
  [XLSX_MEDIA_TYPE]: { extension: "xlsx", maxBytes: 20 * MIB, supportMaterialOnly: true, downloadable: true },
  [PPTX_MEDIA_TYPE]: { extension: "pptx", maxBytes: 40 * MIB, supportMaterialOnly: true, downloadable: true },
  "text/plain": { extension: "txt", maxBytes: 2 * MIB, supportMaterialOnly: true, downloadable: true },
  "text/markdown": { extension: "md", maxBytes: 2 * MIB, supportMaterialOnly: true, downloadable: true },
  "text/csv": { extension: "csv", maxBytes: 5 * MIB, supportMaterialOnly: true, downloadable: true },
  "application/json": { extension: "json", maxBytes: 5 * MIB, supportMaterialOnly: true, downloadable: true }
};

export type AssetMediaType = keyof typeof SUPPORTED_ASSET_MEDIA & string;

export const MAX_PDF_BYTES = SUPPORTED_ASSET_MEDIA[PDF_MEDIA_TYPE].maxBytes;
export const MAX_VIDEO_BYTES = SUPPORTED_ASSET_MEDIA[VIDEO_MEDIA_TYPE].maxBytes;
export const MAX_ASSET_BYTES = Math.max(
  ...Object.values(SUPPORTED_ASSET_MEDIA).map((spec) => spec.maxBytes)
);

export const ASSET_UPLOAD_ACCEPT = Object.entries(SUPPORTED_ASSET_MEDIA)
  .flatMap(([mediaType, spec]) => [mediaType, `.${spec.extension}`])
  .join(",");

export function assetMediaSpec(mediaType: unknown): AssetMediaSpec | null {
  if (typeof mediaType !== "string") return null;
  return SUPPORTED_ASSET_MEDIA[mediaType] ?? null;
}

export function assetMediaLimit(mediaType: unknown) {
  return assetMediaSpec(mediaType)?.maxBytes ?? 0;
}

export function isDocumentMedia(mediaType: unknown) {
  return assetMediaSpec(mediaType)?.supportMaterialOnly === true;
}

export function isDownloadableMedia(mediaType: unknown) {
  return assetMediaSpec(mediaType)?.downloadable === true;
}

/** Resolve the media type a picked file claims to be, refusing any disagreement. */
export function assetMediaTypeForFile(fileName: string, browserType: string): AssetMediaType | null {
  const lower = fileName.toLowerCase();
  for (const [mediaType, spec] of Object.entries(SUPPORTED_ASSET_MEDIA)) {
    if (!lower.endsWith(`.${spec.extension}`)) continue;
    // An empty browser type is common for the office and text formats, so it is
    // accepted; a browser type that names a different format is not.
    if (browserType && browserType !== mediaType) return null;
    return mediaType;
  }
  return null;
}

export function validAssetMediaFile(
  fileName: unknown,
  mediaType: unknown,
  byteSize: unknown
): mediaType is AssetMediaType {
  if (
    typeof fileName !== "string"
    || typeof byteSize !== "number"
    || !Number.isSafeInteger(byteSize)
    || byteSize < 1
  ) return false;
  const spec = assetMediaSpec(mediaType);
  if (!spec) return false;
  return fileName.toLowerCase().endsWith(`.${spec.extension}`) && byteSize <= spec.maxBytes;
}
