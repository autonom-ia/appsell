import { NextResponse } from "next/server";

import { readPublicStorefront } from "@/lib/internal-api-client.server";
import { parsePublicStorefront } from "@/lib/public-storefront";

export const dynamic = "force-dynamic";

const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const MAX_COVER_BYTES = 5 * 1024 * 1024;

interface CoverRouteContext {
  params: Promise<{ sellerSlug: string; productSlug: string }>;
}

export async function GET(_request: Request, context: CoverRouteContext) {
  const { sellerSlug, productSlug } = await context.params;
  if (!SLUG.test(sellerSlug) || !SLUG.test(productSlug)) return notFound();

  try {
    const storefront = parsePublicStorefront(await readPublicStorefront(sellerSlug));
    const cover = storefront?.products.find((product) => product.slug === productSlug)?.cover;
    if (!cover) return notFound();

    const response = await fetch(cover.url, { cache: "no-store", redirect: "error" });
    const contentType = response.headers.get("content-type")?.split(";", 1)[0]?.trim() ?? "";
    const contentLength = Number(response.headers.get("content-length") ?? "0");
    if (!response.ok || contentType !== cover.media_type || !contentType.startsWith("image/") ||
        (contentLength > 0 && contentLength > MAX_COVER_BYTES)) {
      return unavailable();
    }

    const body = await response.arrayBuffer();
    if (body.byteLength === 0 || body.byteLength > MAX_COVER_BYTES) return unavailable();
    return new NextResponse(body, {
      headers: {
        "cache-control": "public, max-age=60, stale-while-revalidate=300",
        "content-length": String(body.byteLength),
        "content-type": contentType,
        "x-content-type-options": "nosniff"
      }
    });
  } catch {
    return unavailable();
  }
}

function notFound() {
  return NextResponse.json({ error: "cover_not_found" }, {
    status: 404,
    headers: { "cache-control": "no-store" }
  });
}

function unavailable() {
  return NextResponse.json({ error: "cover_unavailable" }, {
    status: 503,
    headers: { "cache-control": "no-store" }
  });
}
