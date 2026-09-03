import { notFound, serveStorefrontImage } from "@/lib/storefront-image-proxy.server";

export const dynamic = "force-dynamic";

const KINDS = new Set(["logo", "banner"]);

interface BrandRouteContext {
  params: Promise<{ sellerSlug: string; kind: string }>;
}

/** Logo e banner da loja por um endereço estável, para o card de quem
 *  compartilha o link (#412). */
export async function GET(_request: Request, context: BrandRouteContext) {
  const { sellerSlug, kind } = await context.params;
  if (!KINDS.has(kind)) return notFound();
  return serveStorefrontImage(sellerSlug, (storefront) =>
    kind === "logo" ? storefront.seller.logo : storefront.seller.banner);
}
