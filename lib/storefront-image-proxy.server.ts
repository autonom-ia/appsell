import "server-only";

import { NextResponse } from "next/server";

import { readPublicStorefront } from "@/lib/internal-api-client.server";
import { parsePublicStorefront, type PublicStorefront, type StorefrontImage } from "@/lib/public-storefront";

/** Serve uma imagem pública da loja por um endereço estável.
 *
 * A URL que a API devolve é assinada e vale 60 segundos: serve para a página
 * renderizar agora, não para o robô de um mensageiro buscar depois. Esta função
 * lê a URL fresca no servidor e devolve os bytes, sem nunca expor a chave do
 * objeto nem a assinatura. Nasceu na rota da capa (#396) e virou uma só quando
 * logo, banner da loja e banner do produto precisaram do mesmo (#412).
 */

const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

export function isSlug(value: string): boolean {
  return SLUG.test(value);
}

export async function serveStorefrontImage(
  sellerSlug: string,
  escolher: (storefront: PublicStorefront) => StorefrontImage | undefined,
  // O checkout externo já lê os erros da rota da capa como `cover_*`; o nome
  // do recurso continua saindo no corpo para não quebrar quem depende disso.
  recurso = "image"
): Promise<NextResponse> {
  if (!isSlug(sellerSlug)) return notFound(recurso);

  try {
    const storefront = parsePublicStorefront(await readPublicStorefront(sellerSlug));
    const image = storefront ? escolher(storefront) : undefined;
    if (!image) return notFound(recurso);

    const response = await fetch(image.url, { cache: "no-store", redirect: "error" });
    const contentType = response.headers.get("content-type")?.split(";", 1)[0]?.trim() ?? "";
    const contentLength = Number(response.headers.get("content-length") ?? "0");
    if (!response.ok || contentType !== image.media_type || !contentType.startsWith("image/") ||
        (contentLength > 0 && contentLength > MAX_IMAGE_BYTES)) {
      return unavailable(recurso);
    }

    const body = await response.arrayBuffer();
    if (body.byteLength === 0 || body.byteLength > MAX_IMAGE_BYTES) return unavailable(recurso);
    return new NextResponse(body, {
      headers: {
        "cache-control": "public, max-age=60, stale-while-revalidate=300",
        "content-length": String(body.byteLength),
        "content-type": contentType,
        "cross-origin-resource-policy": "cross-origin",
        "x-content-type-options": "nosniff"
      }
    });
  } catch {
    return unavailable(recurso);
  }
}

export function notFound(recurso = "image"): NextResponse {
  return NextResponse.json({ error: `${recurso}_not_found` }, {
    status: 404,
    headers: { "cache-control": "no-store" }
  });
}

function unavailable(recurso: string): NextResponse {
  return NextResponse.json({ error: `${recurso}_unavailable` }, {
    status: 503,
    headers: { "cache-control": "no-store" }
  });
}
