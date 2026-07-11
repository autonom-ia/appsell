import { NextRequest, NextResponse } from "next/server";

import { previewDomainProductDraft } from "@/lib/internal-api-client.server";

import {
  authenticatedSession,
  mappedError,
  notFound,
  productRouteIds,
  type ProductRouteContext,
  unauthorized
} from "../_route-shared";

export async function GET(request: NextRequest, context: ProductRouteContext) {
  const ids = await productRouteIds(context);
  if (!ids) return notFound();
  try {
    const authenticated = await authenticatedSession(request);
    if (!authenticated) return unauthorized();
    const payload = await previewDomainProductDraft(
      authenticated.sessionId,
      authenticated.session,
      ids.sellerId,
      ids.productId
    );
    return NextResponse.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}
