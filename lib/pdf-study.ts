export type PdfStudyTool = "pen" | "highlight";

export interface PdfStudyPoint {
  x: number;
  y: number;
}

export interface PdfStudyAnnotation {
  id: string;
  page: number;
  tool: PdfStudyTool;
  color: string;
  width: number;
  points: PdfStudyPoint[];
}

export interface PdfStudyState {
  asset_id: string;
  asset_version: number;
  page_count: number;
  revision: number;
  annotations: PdfStudyAnnotation[];
  bookmarks: number[];
  updated_at: string | null;
}

export type PdfStudyMutation =
  | { action: "annotation.upsert"; annotation: PdfStudyAnnotation }
  | { action: "annotation.delete"; id: string }
  | { action: "bookmark.set"; page: number; active: boolean };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const COLOR = /^#[0-9A-Fa-f]{6}$/;

export function parsePdfStudyEnvelope(value: unknown, assetId: string, pageCount?: number) {
  if (!exactRecord(value, ["state"])) return null;
  return parsePdfStudyState(value.state, assetId, pageCount);
}

export function parsePdfStudyState(value: unknown, assetId: string, expectedPageCount?: number) {
  if (!exactRecord(value, [
    "annotations", "asset_id", "asset_version", "bookmarks", "page_count",
    "revision", "updated_at"
  ])) return null;
  if (value.asset_id !== assetId || !positiveInteger(value.asset_version) ||
    !boundedInteger(value.page_count, 1, 100) ||
    (expectedPageCount !== undefined && value.page_count !== expectedPageCount) ||
    !boundedInteger(value.revision, 0, Number.MAX_SAFE_INTEGER) ||
    !(value.updated_at === null || typeof value.updated_at === "string") ||
    !Array.isArray(value.annotations) || value.annotations.length > 10_000 ||
    !value.annotations.every((annotation) => validAnnotation(annotation, Number(value.page_count))) ||
    !Array.isArray(value.bookmarks) || value.bookmarks.length > 100 ||
    !value.bookmarks.every((page) => boundedInteger(page, 1, Number(value.page_count))) ||
    new Set(value.bookmarks).size !== value.bookmarks.length) return null;
  return value as unknown as PdfStudyState;
}

export function validPdfStudyMutation(value: unknown, pageCount = 100): value is PdfStudyMutation {
  if (!record(value) || typeof value.action !== "string") return false;
  if (value.action === "annotation.upsert") {
    return exactRecord(value, ["action", "annotation"]) &&
      validAnnotation(value.annotation, pageCount);
  }
  if (value.action === "annotation.delete") {
    return exactRecord(value, ["action", "id"]) && typeof value.id === "string" && UUID.test(value.id);
  }
  return value.action === "bookmark.set" && exactRecord(value, ["action", "active", "page"]) &&
    typeof value.active === "boolean" && boundedInteger(value.page, 1, pageCount);
}

function validAnnotation(value: unknown, pageCount: number): value is PdfStudyAnnotation {
  return exactRecord(value, ["color", "id", "page", "points", "tool", "width"]) &&
    typeof value.id === "string" && UUID.test(value.id) &&
    boundedInteger(value.page, 1, pageCount) &&
    (value.tool === "pen" || value.tool === "highlight") &&
    typeof value.color === "string" && COLOR.test(value.color) &&
    finiteRange(value.width, 0.0005, 0.05) && Array.isArray(value.points) &&
    value.points.length >= 2 && value.points.length <= 1024 &&
    value.points.every((point) => exactRecord(point, ["x", "y"]) &&
      finiteRange(point.x, 0, 1) && finiteRange(point.y, 0, 1));
}

function exactRecord(value: unknown, keys: string[]): value is Record<string, unknown> {
  if (!record(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function boundedInteger(value: unknown, minimum: number, maximum: number) {
  return Number.isSafeInteger(value) && Number(value) >= minimum && Number(value) <= maximum;
}

function positiveInteger(value: unknown) {
  return boundedInteger(value, 1, Number.MAX_SAFE_INTEGER);
}

function finiteRange(value: unknown, minimum: number, maximum: number) {
  return typeof value === "number" && Number.isFinite(value) && value >= minimum && value <= maximum;
}
