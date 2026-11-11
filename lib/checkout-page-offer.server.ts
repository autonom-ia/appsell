import "server-only";

import { DomainApiError, readPublicStorefront } from "@/lib/internal-api-client.server";
import { isProductCheckoutAvailable } from "@/lib/product-checkout-availability.server";
import { parsePublicStorefront } from "@/lib/public-storefront";

// O produto e o preço que a página de checkout mostra, lidos da mesma projeção
// pública que a rota de cobrança usa, nunca do cliente.
//
// "A loja não respondeu" e "este produto não está à venda" são casos diferentes
// (#154, achado 3): o primeiro passa se o comprador tentar de novo, o segundo
// não. Antes, os dois viravam a mesma mensagem.
export type CheckoutPageOffer =
  | { kind: "ready"; sellerName: string; productTitle: string; priceLabel: string }
  | { kind: "unavailable" }
  | { kind: "not_for_sale" };

const UNAVAILABLE: CheckoutPageOffer = Object.freeze({ kind: "unavailable" });
const NOT_FOR_SALE: CheckoutPageOffer = Object.freeze({ kind: "not_for_sale" });

export async function readCheckoutPageOffer(sellerSlug: string, productSlug: string): Promise<CheckoutPageOffer> {
  if (!isProductCheckoutAvailable(sellerSlug, productSlug)) return NOT_FOR_SALE;
  let storefront: unknown;
  try {
    storefront = await readPublicStorefront(sellerSlug);
  } catch (error) {
    // 404: a loja não existe. Qualquer outra falha é a api fora do ar.
    return error instanceof DomainApiError && error.status === 404 ? NOT_FOR_SALE : UNAVAILABLE;
  }
  const parsed = parsePublicStorefront(storefront);
  if (!parsed) return UNAVAILABLE;
  const found = parsed.products.find((candidate) => candidate.slug === productSlug);
  const price = found?.price;
  // Mesmo portão da rota create-session: só preço único, em real, positivo.
  if (!found || !price) return NOT_FOR_SALE;
  if (price.billing_period !== "one_time" || price.currency !== "BRL") return NOT_FOR_SALE;
  if (!Number.isInteger(price.unit_amount_minor) || price.unit_amount_minor <= 0) return NOT_FOR_SALE;
  return {
    kind: "ready",
    sellerName: parsed.seller.name,
    productTitle: found.title,
    priceLabel: new Intl.NumberFormat("pt-BR", { style: "currency", currency: price.currency })
      .format(price.unit_amount_minor / 100)
  };
}
