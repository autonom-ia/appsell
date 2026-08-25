import { NextRequest, NextResponse } from "next/server";

import { clearDomainProductCover } from "@/lib/internal-api-client.server";
import {
  invalidInput,
  mappedError,
  notFound
} from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import {
  coverActions,
  coverIds,
  parseCoverMutation,
  prepareCoverMutation,
  type CoverRouteContext
} from "../_route-shared";

const REQUIRED = ["_csrf", "idempotency_key", "expected_product_version"] as const;

export async function POST(request: NextRequest, context: CoverRouteContext) {
  const ids = await coverIds(context);
  if (!ids) return notFound();
  const prepared = await prepareCoverMutation(request, coverActions.clear, REQUIRED);
  if (!prepared.ok) return prepared.response;
  const parsed = parseCoverMutation(prepared.fields);
  if (!parsed) return invalidInput("form");
  try {
    const payload = await clearDomainProductCover(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      { ...ids, ...parsed }
    );
    return NextResponse.json(payload, { status: 200, headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}
