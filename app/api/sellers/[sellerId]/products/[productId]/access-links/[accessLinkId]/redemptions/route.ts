import { NextRequest, NextResponse } from "next/server";

import { listDomainAccessLinkRedemptions } from "@/lib/internal-api-client.server";

import {
  authenticatedSession,
  invalidInput,
  mappedError,
  unauthorized
} from "../../../_route-shared";
import { accessLinkRouteIds, type AccessLinkRouteContext } from "../_shared";

export async function GET(request: NextRequest, context: AccessLinkRouteContext) {
  const ids = await accessLinkRouteIds(context);
  if (!ids) return invalidInput("form");
  let authenticated: Awaited<ReturnType<typeof authenticatedSession>>;
  try {
    authenticated = await authenticatedSession(request);
  } catch (error) {
    return mappedError(error);
  }
  if (!authenticated) return unauthorized();
  try {
    const payload = await listDomainAccessLinkRedemptions(
      authenticated.sessionId,
      authenticated.session,
      ids.sellerId,
      ids.productId,
      ids.accessLinkId
    );
    return NextResponse.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}
