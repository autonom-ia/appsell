import Link from "next/link";
import { notFound } from "next/navigation";

import { CheckoutWorkspace } from "@/app/components/checkout-workspace";
import { resolveCheckoutProduct } from "@/lib/checkout-config";
import {
  DomainApiError,
  readPublicStorefront
} from "@/lib/internal-api-client.server";
import { parsePublicStorefront } from "@/lib/public-storefront";

export const dynamic = "force-dynamic";

export default async function ProductCheckoutPage({
  params
}: {
  params: Promise<{ sellerSlug: string; productSlug: string }>;
}) {
  const { sellerSlug, productSlug } = await params;
  const storefront = await loadStorefront(sellerSlug);
  const product = storefront.products.find((item) => item.slug === productSlug);
  if (!product || !product.price) notFound();

  const checkoutProduct = resolveCheckoutProduct(sellerSlug, productSlug);
  const priceLabel = formatPrice(product.price.unit_amount_minor, product.price.currency);

  return <div className="storefront-page product-checkout-page">
    <a className="skip-link" href="#product-checkout-content">Pular para o produto</a>
    <header className="storefront-topbar">
      <Link className="landing-brand" href={`/s/${storefront.seller.slug}`} aria-label={`${storefront.seller.name} — voltar à loja`}>
        <span className="brand-mark" aria-hidden="true">a</span>
        <strong>Autonom.ia <span>Sell</span></strong>
      </Link>
      <Link className="storefront-login" href="/auth/login">Entrar</Link>
    </header>
    <main id="product-checkout-content" tabIndex={-1} className="product-checkout-main">
      <section className="product-checkout-summary" aria-labelledby="product-checkout-title">
        <p className="eyebrow">{storefront.seller.name}</p>
        <h1 id="product-checkout-title">{product.title}</h1>
        {product.description.trim() ? <p>{product.description}</p> : null}
        <p className="product-checkout-price">{priceLabel}</p>
      </section>
      <section className="product-checkout-action" aria-label="Finalizar compra">
        {checkoutProduct
          ? <CheckoutWorkspace
              productTitle={product.title}
              priceLabel={priceLabel}
              financialProductKey={checkoutProduct.financialProductKey}
            />
          : <div className="checkout-card checkout-unavailable" role="alert">
              <h2>Checkout indisponível</h2>
              <p>Este produto ainda não está liberado para compra direta. Tente novamente mais tarde ou entre em contato com o produtor.</p>
            </div>}
      </section>
    </main>
  </div>;
}

async function loadStorefront(sellerSlug: string) {
  try {
    const parsed = parsePublicStorefront(await readPublicStorefront(sellerSlug));
    if (!parsed) throw new DomainApiError(503, "invalid_public_storefront");
    return parsed;
  } catch (error) {
    if (error instanceof DomainApiError && error.status === 404) notFound();
    throw error;
  }
}

function formatPrice(unitAmountMinor: number, currency: string): string {
  return new Intl.NumberFormat("pt-BR", { style: "currency", currency }).format(unitAmountMinor / 100);
}
