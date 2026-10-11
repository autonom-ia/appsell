import { NextResponse } from "next/server";

import { DomainApiError } from "@/lib/internal-api-client.server";
import { readPublicProduct } from "@/lib/public-storefront.server";
import { isPublicSlug } from "@/lib/public-storefront";

export async function GET(
  _request: Request,
  context: { params: Promise<{ sellerSlug: string; productSlug: string }> }
) {
  const { sellerSlug, productSlug } = await context.params;
  if (!isPublicSlug(sellerSlug) || !isPublicSlug(productSlug)) return notFound();
  try {
    return NextResponse.json(await readPublicProduct(sellerSlug, productSlug), {
      headers: publicNoStoreHeaders()
    });
  } catch (error) {
    if (error instanceof DomainApiError && error.status === 404) return notFound();
    const status = error instanceof DomainApiError && error.status === 502 ? 502 : 503;
    return NextResponse.json({ error: "storefront_unavailable" }, {
      status,
      headers: publicNoStoreHeaders()
    });
  }
}

function notFound() {
  return NextResponse.json({ error: "product_not_found" }, {
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
