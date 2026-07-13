import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import {
  DomainApiError,
  readDomainBuyerProducts,
  readDomainBuyerSellerProducts
} from "@/lib/internal-api-client.server";
import {
  parseBuyerProductsPayload,
  parseBuyerSellerProductsPayload
} from "@/lib/buyer-products";
import { oauthService } from "@/lib/oauth-service.server";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";

export async function authenticatedLibrarySession(request: NextRequest) {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId) return null;
  const session = await oauthService().getSession(sessionId);
  return session ? { sessionId, session } : null;
}

export function libraryError(error: unknown) {
  const status = error instanceof DomainApiError ? error.status : 503;
  const code = error instanceof DomainApiError
    ? error.code
    : isOAuthStoreUnavailable(error)
      ? "oauth_store_unavailable"
      : "library_unavailable";
  return NextResponse.json({ error: code }, {
    status,
    headers: { "cache-control": "private, no-store" }
  });
}

export function libraryUnauthorized() {
  return NextResponse.json({ error: "unauthorized" }, {
    status: 401,
    headers: { "cache-control": "private, no-store" }
  });
}

export async function buyerProductsResponse(request: NextRequest) {
  try {
    const authenticated = await authenticatedLibrarySession(request);
    if (!authenticated) return libraryUnauthorized();
    const raw = await readDomainBuyerProducts(authenticated.sessionId, authenticated.session);
    const payload = parseBuyerProductsPayload(raw);
    if (!payload) return invalidLibraryResponse();
    return NextResponse.json(payload, {
      headers: { "cache-control": "private, no-store" }
    });
  } catch (error) {
    return libraryError(error);
  }
}

export async function buyerSellerProductsResponse(
  request: NextRequest,
  entitlementId: string
) {
  try {
    const authenticated = await authenticatedLibrarySession(request);
    if (!authenticated) return libraryUnauthorized();
    if (!isCanonicalUuid(entitlementId)) return libraryNotFound();
    const raw = await readDomainBuyerSellerProducts(
      authenticated.sessionId,
      authenticated.session,
      entitlementId
    );
    const payload = parseBuyerSellerProductsPayload(raw);
    if (!payload) return invalidLibraryResponse();
    return NextResponse.json(payload, {
      headers: { "cache-control": "private, no-store" }
    });
  } catch (error) {
    return libraryError(error);
  }
}

function invalidLibraryResponse() {
  return NextResponse.json({ error: "invalid_library_response" }, {
    status: 503,
    headers: { "cache-control": "private, no-store" }
  });
}

function libraryNotFound() {
  return NextResponse.json({ error: "learning_item_unavailable" }, {
    status: 404,
    headers: { "cache-control": "private, no-store" }
  });
}

function isCanonicalUuid(value: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);
}
