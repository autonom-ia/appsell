import { NextRequest, NextResponse } from "next/server";

import { DomainApiError } from "@/lib/internal-api-client.server";
import { readPublicStorefront } from "@/lib/public-storefront.server";
import { isPublicSlug } from "@/lib/public-storefront";

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ sellerSlug: string }> }
) {
  const { sellerSlug } = await context.params;
  const cursor = request.nextUrl.searchParams.get("cursor");
  const rawLimit = request.nextUrl.searchParams.get("limit");
  const limit = rawLimit === null ? 12 : Number(rawLimit);
  if (!isPublicSlug(sellerSlug) || (cursor !== null && !isPublicSlug(cursor)) ||
      !Number.isSafeInteger(limit) || limit < 1 || limit > 24) return notFound();
  try {
    return NextResponse.json(await readPublicStorefront(sellerSlug, cursor, limit), {
      headers: publicNoStoreHeaders()
    });
  } catch (error) {
    return publicError(error);
  }
}

function publicError(error: unknown) {
  if (error instanceof DomainApiError && error.status === 404) return notFound();
  const status = error instanceof DomainApiError && error.status === 502 ? 502 : 503;
  return NextResponse.json({ error: "storefront_unavailable" }, {
    status,
    headers: publicNoStoreHeaders()
  });
}

function notFound() {
  return NextResponse.json({ error: "storefront_not_found" }, {
    status: 404,
    headers: publicNoStoreHeaders()
  });
}

function publicNoStoreHeaders() {
  return {
    "cache-control": "no-store, max-age=0",
    "cdn-cache-control": "no-store",
    "surrogate-control": "no-store"
  };
}
