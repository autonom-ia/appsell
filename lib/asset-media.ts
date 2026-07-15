export const MAX_PDF_BYTES = 10 * 1024 * 1024;
export const MAX_VIDEO_BYTES = 100 * 1024 * 1024;
export type AssetMediaType = "application/pdf" | "video/mp4";

export function assetMediaLimit(mediaType: unknown) {
  if (mediaType === "application/pdf") return MAX_PDF_BYTES;
  if (mediaType === "video/mp4") return MAX_VIDEO_BYTES;
  return 0;
}

export function validAssetMediaFile(fileName: unknown, mediaType: unknown, byteSize: unknown): mediaType is AssetMediaType {
  if (typeof fileName !== "string" || typeof byteSize !== "number" || !Number.isSafeInteger(byteSize) || byteSize < 1) return false;
  if (mediaType === "application/pdf") return fileName.toLowerCase().endsWith(".pdf") && byteSize <= MAX_PDF_BYTES;
  if (mediaType === "video/mp4") return fileName.toLowerCase().endsWith(".mp4") && byteSize <= MAX_VIDEO_BYTES;
  return false;
}
