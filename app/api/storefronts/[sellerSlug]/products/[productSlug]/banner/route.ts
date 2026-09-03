import { isSlug, notFound, serveStorefrontImage } from "@/lib/storefront-image-proxy.server";

export const dynamic = "force-dynamic";

interface BannerRouteContext {
  params: Promise<{ sellerSlug: string; productSlug: string }>;
}

/** O banner do produto por um endereço estável, do mesmo jeito que a capa
 *  (#412). */
export async function GET(_request: Request, context: BannerRouteContext) {
  const { sellerSlug, productSlug } = await context.params;
  if (!isSlug(productSlug)) return notFound();
  return serveStorefrontImage(sellerSlug, (storefront) =>
    storefront.products.find((product) => product.slug === productSlug)?.banner);
}
