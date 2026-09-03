import { isSlug, notFound, serveStorefrontImage } from "@/lib/storefront-image-proxy.server";

export const dynamic = "force-dynamic";

interface CoverRouteContext {
  params: Promise<{ sellerSlug: string; productSlug: string }>;
}

/** A capa do produto por um endereço estável: a URL que a API devolve é
 *  assinada e expira, e o checkout externo precisa carregar a imagem depois
 *  (#396). A lógica mora em `serveStorefrontImage`, junto com a da marca. */
export async function GET(_request: Request, context: CoverRouteContext) {
  const { sellerSlug, productSlug } = await context.params;
  if (!isSlug(productSlug)) return notFound("cover");
  return serveStorefrontImage(
    sellerSlug,
    (storefront) => storefront.products.find((product) => product.slug === productSlug)?.cover,
    "cover"
  );
}
