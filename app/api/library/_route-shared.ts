import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import {
  DomainApiError,
  readDomainBuyerProducts,
  readDomainBuyerSellerProducts
} from "@/lib/internal-api-client.server";
import {
  parseBuyerProductsPayload,
  parseBuyerSellerProductsPayload,
  type BuyerProductItem
} from "@/lib/buyer-products";
import { oauthService } from "@/lib/oauth-service.server";
import { configuredStorageUploadOrigin, trustedProductCover } from "@/lib/storage-upload-policy.server";
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
    return NextResponse.json(comCapaConfiavel(payload), {
      headers: { "cache-control": "private, no-store" }
    });
  } catch (error) {
    return libraryError(error);
  }
}

/**
 * A capa só chega ao navegador se vier do armazenamento que configuramos.
 *
 * O mesmo portão do painel do vendedor: uma imagem que aponte para outro
 * lugar vira requisição do navegador de quem estuda para um host qualquer.
 * Uma capa recusada some, e o cartão desenha a arte gerada a partir do nome.
 */
function comCapaConfiavel(payload: { items: BuyerProductItem[] }) {
  let origem: string;
  try {
    origem = configuredStorageUploadOrigin();
  } catch {
    origem = "";
  }
  return {
    items: payload.items.map((item) => {
      if (!item.product.cover) return item;
      const confiavel = origem ? trustedProductCover(item.product.cover, origem) : null;
      if (confiavel) return item;
      const produto = { ...item.product };
      delete produto.cover;
      return { ...item, product: produto };
    })
  };
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
