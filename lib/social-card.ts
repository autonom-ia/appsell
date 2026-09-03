/** A imagem que vai no card de quem compartilha um link do Sell.
 *
 * Regra da Decisão 021, aplicada ao card: uma imagem, nunca duas, e sempre a
 * mais próxima do assunto do link. Loja mostra o banner dela; produto mostra o
 * banner do produto e, na falta, a capa. Só quando o produto não tem imagem
 * nenhuma é que a marca da loja entra, para nenhum link cair no ícone da
 * plataforma — que é o defeito que originou isto (#412).
 *
 * O endereço é sempre o da rota pública do web, nunca a URL assinada do
 * armazenamento: a assinada vale 60 segundos e o robô do WhatsApp busca a
 * imagem depois do envio.
 */
import type { PublicStorefrontProduct, PublicStorefrontSeller, StorefrontImage } from "@/lib/public-storefront";
import { SITE } from "@/lib/site";

export interface SocialCardImage {
  url: string;
  width: number;
  height: number;
  alt: string;
}

/** Proporção a partir da qual o WhatsApp desenha o card largo, em vez da
 *  miniatura ao lado do texto. */
const PROPORCAO_DO_CARD_LARGO = 1.7;

export function storefrontCardImage(seller: PublicStorefrontSeller): SocialCardImage | undefined {
  const loja = encodeURIComponent(seller.slug);
  if (seller.banner) return imagem(`/api/storefronts/${loja}/brand/banner`, seller.banner, `Banner de ${seller.name}`);
  if (seller.logo) return imagem(`/api/storefronts/${loja}/brand/logo`, seller.logo, `Logo de ${seller.name}`);
  return undefined;
}

export function productCardImage(
  seller: PublicStorefrontSeller,
  product: PublicStorefrontProduct
): SocialCardImage | undefined {
  const loja = encodeURIComponent(seller.slug);
  const produto = encodeURIComponent(product.slug);
  if (product.banner) {
    return imagem(`/api/storefronts/${loja}/products/${produto}/banner`, product.banner, `Banner de ${product.title}`);
  }
  if (product.cover) {
    return imagem(`/api/storefronts/${loja}/products/${produto}/cover`, product.cover, `Capa de ${product.title}`);
  }
  return storefrontCardImage(seller);
}

/** `summary_large_image` só quando a imagem é larga de verdade: um logo
 *  quadrado esticado em card largo fica pior do que a miniatura. */
export function twitterCardKind(image: SocialCardImage | undefined): "summary" | "summary_large_image" {
  if (!image || image.height <= 0) return "summary";
  return image.width / image.height >= PROPORCAO_DO_CARD_LARGO ? "summary_large_image" : "summary";
}

function imagem(caminho: string, origem: StorefrontImage, alt: string): SocialCardImage {
  return { url: `${SITE.origem}${caminho}`, width: origem.width, height: origem.height, alt };
}
