import { MAX_PDF_PAGES } from "@/lib/pdf-study";
const LINK_ID = /^[0-9a-f]{16}$/;
/**
 * Os containers que o leitor abre no player.
 *
 * Espelha `VIDEO_EXTENSIONS` em `pdf_links.py`, que é quem classifica o link na
 * extração. Divergir aqui faz o BFF recusar um alvo que o domínio já marcou
 * como vídeo, e o aluno recebe 503 sem explicação.
 */
const VIDEO_PATH = /\.(?:mov|mp4|m4v|webm|mkv|avi|mpg|mpeg|wmv|flv|3gp|ogv|mts|m2ts|ts)$/i;

export interface PdfLinkRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export type DomainPdfLink = {
  id: string;
  page: number;
  rect: PdfLinkRect;
} & (
  | { kind: "page"; target_page: number }
  | { kind: "video" | "external"; target_url: string }
);

export interface DomainPdfManifest {
  asset: {
    id: string;
    page_count: number;
    links: DomainPdfLink[];
  };
}

export function parseDomainPdfManifest(
  value: unknown,
  expectedAssetId: string
): DomainPdfManifest | null {
  if (!isRecord(value) || Object.keys(value).length !== 1 || !isRecord(value.asset)) return null;
  const asset = value.asset;
  if (Object.keys(asset).sort().join(",") !== "id,links,page_count" ||
    asset.id !== expectedAssetId || !Array.isArray(asset.links) || asset.links.length > 512 ||
    !Number.isSafeInteger(asset.page_count) || Number(asset.page_count) < 1 ||
    Number(asset.page_count) > MAX_PDF_PAGES) return null;
  const pageCount = Number(asset.page_count);
  if (!asset.links.every((link) => validDomainPdfLink(link, pageCount))) return null;
  return { asset: asset as unknown as DomainPdfManifest["asset"] };
}

export function publicPdfManifest(value: DomainPdfManifest, baseHref: string) {
  return {
    asset_id: value.asset.id,
    page_count: value.asset.page_count,
    links: value.asset.links.map((link) => {
      const common = { id: link.id, page: link.page, kind: link.kind, rect: link.rect };
      if (link.kind === "page") return { ...common, target_page: link.target_page };
      if (link.kind === "video") {
        return { ...common, media_href: `${baseHref}/links/${link.id}/media` };
      }
      return { ...common, href: link.target_url };
    })
  };
}

export function trustedPdfVideoTarget(value: string) {
  let target: URL;
  try {
    target = new URL(value);
  } catch {
    return null;
  }
  if (target.protocol !== "https:" || target.username || target.password ||
    (target.port && target.port !== "443") || !VIDEO_PATH.test(target.pathname)) return null;
  const origins = (process.env.APPSELL_PDF_VIDEO_ALLOWED_ORIGINS ?? "")
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean);
  if (!origins.some((origin) => validConfiguredOrigin(origin, target.origin))) return null;
  return target;
}

export function safeVideoRange(value: string | null) {
  if (value === null) return null;
  return /^bytes=(?:\d+-\d*|-\d+)$/.test(value) && value.length <= 80 ? value : undefined;
}

function validDomainPdfLink(value: unknown, pageCount: number): value is DomainPdfLink {
  if (!isRecord(value) || typeof value.id !== "string" || !LINK_ID.test(value.id) ||
    !Number.isSafeInteger(value.page) || Number(value.page) < 1 || Number(value.page) > pageCount ||
    !validRect(value.rect)) return false;
  const common = ["id", "kind", "page", "rect"];
  if (value.kind === "page") {
    return Object.keys(value).sort().join(",") === [...common, "target_page"].sort().join(",") &&
      Number.isSafeInteger(value.target_page) && Number(value.target_page) >= 1 &&
      Number(value.target_page) <= pageCount;
  }
  if (value.kind === "video" || value.kind === "external") {
    return Object.keys(value).sort().join(",") === [...common, "target_url"].sort().join(",") &&
      typeof value.target_url === "string" && safeHttpsUrl(value.target_url);
  }
  return false;
}

function validRect(value: unknown): value is PdfLinkRect {
  if (!isRecord(value) || Object.keys(value).sort().join(",") !== "height,left,top,width") return false;
  const numbers = [value.left, value.top, value.width, value.height];
  return numbers.every((entry) => typeof entry === "number" && Number.isFinite(entry) &&
    entry >= 0 && entry <= 100) && Number(value.width) > 0 && Number(value.height) > 0 &&
    Number(value.left) + Number(value.width) <= 100.01 &&
    Number(value.top) + Number(value.height) <= 100.01;
}

function safeHttpsUrl(value: string) {
  if (value.length < 1 || value.length > 2_048) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password &&
      (!url.port || url.port === "443");
  } catch {
    return false;
  }
}

function validConfiguredOrigin(value: string, expected: string) {
  try {
    const origin = new URL(value);
    return origin.origin === expected && origin.protocol === "https:" &&
      origin.pathname === "/" && !origin.search && !origin.hash &&
      !origin.username && !origin.password;
  } catch {
    return false;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
