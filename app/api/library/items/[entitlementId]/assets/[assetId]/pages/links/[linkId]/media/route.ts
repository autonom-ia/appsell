import { NextRequest, NextResponse } from "next/server";

import { readDomainLearningPdfManifest } from "@/lib/internal-api-client.server";
import {
  parseDomainPdfManifest,
  safeVideoRange,
  trustedPdfVideoTarget
} from "@/lib/protected-pdf-links.server";
import {
  authenticatedLearningSession,
  learningAssetLinkIds,
  learningError,
  learningNotFound,
  learningUnauthorized,
  type LearningAssetLinkRouteContext
} from "../../../../../../../_route-shared";

const MAX_VIDEO_BYTES = 100 * 1024 * 1024;
const VIDEO_TYPES = new Set(["video/mp4", "video/quicktime"]);

export async function GET(request: NextRequest, context: LearningAssetLinkRouteContext) {
  return proxyPdfVideo(request, context);
}

export async function HEAD(request: NextRequest, context: LearningAssetLinkRouteContext) {
  return proxyPdfVideo(request, context);
}

async function proxyPdfVideo(request: NextRequest, context: LearningAssetLinkRouteContext) {
  try {
    const authenticated = await authenticatedLearningSession(request);
    if (!authenticated) return learningUnauthorized();
    const ids = await learningAssetLinkIds(context);
    if (!ids) return learningNotFound();
    const payload = await readDomainLearningPdfManifest(
      authenticated.sessionId,
      authenticated.session,
      ids.entitlementId,
      ids.assetId
    );
    const manifest = parseDomainPdfManifest(payload, ids.assetId);
    const link = manifest?.asset.links.find((candidate) =>
      candidate.id === ids.linkId && candidate.kind === "video"
    );
    if (!link || link.kind !== "video") return learningNotFound();
    const target = trustedPdfVideoTarget(link.target_url);
    if (!target) return videoUnavailable();
    const requestedRange = safeVideoRange(request.headers.get("range"));
    if (requestedRange === undefined) return invalidRange();

    const upstream = await fetch(target, {
      method: request.method === "HEAD" ? "HEAD" : "GET",
      headers: requestedRange ? { range: requestedRange } : undefined,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(10 * 60 * 1_000)
    });
    if (![200, 206].includes(upstream.status)) return videoUnavailable();
    const contentType = upstream.headers.get("content-type")?.split(";", 1)[0]?.trim() ?? "";
    const contentLength = Number(upstream.headers.get("content-length"));
    const contentRange = upstream.headers.get("content-range");
    if (!VIDEO_TYPES.has(contentType) || !Number.isSafeInteger(contentLength) ||
      contentLength < 1 || contentLength > MAX_VIDEO_BYTES ||
      !validContentRange(upstream.status, contentRange)) return videoUnavailable();
    const extension = contentType === "video/mp4" ? "mp4" : "mov";
    return new NextResponse(request.method === "HEAD" ? null : upstream.body, {
      status: upstream.status,
      headers: {
        "accept-ranges": "bytes",
        "cache-control": "private, no-store, max-age=0",
        "content-disposition": `inline; filename="video-protegido.${extension}"`,
        "content-length": String(contentLength),
        "content-type": contentType,
        "cross-origin-resource-policy": "same-origin",
        pragma: "no-cache",
        "referrer-policy": "no-referrer",
        "surrogate-control": "no-store",
        vary: "Cookie, Range",
        "x-content-type-options": "nosniff",
        ...(contentRange ? { "content-range": contentRange } : {})
      }
    });
  } catch (error) {
    return learningError(error);
  }
}

function validContentRange(status: number, value: string | null) {
  if (status === 200) return value === null;
  if (value === null) return false;
  const match = value.match(/^bytes (\d+)-(\d+)\/(\d+)$/);
  if (!match) return false;
  const start = Number(match[1]);
  const end = Number(match[2]);
  const total = Number(match[3]);
  return Number.isSafeInteger(start) && Number.isSafeInteger(end) && Number.isSafeInteger(total) &&
    start >= 0 && end >= start && end < total && total <= MAX_VIDEO_BYTES;
}

function invalidRange() {
  return NextResponse.json({ error: "invalid_video_range" }, {
    status: 416,
    headers: { "cache-control": "private, no-store" }
  });
}

function videoUnavailable() {
  return NextResponse.json({ error: "protected_video_unavailable" }, {
    status: 503,
    headers: { "cache-control": "private, no-store" }
  });
}
