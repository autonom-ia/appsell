import Link from "next/link";
import { notFound } from "next/navigation";

import { demoModeEnabled } from "@/lib/demo-config";
import {
  DomainApiError,
  readPublicStorefront
} from "@/lib/internal-api-client.server";
import { billingPeriodLabel, formatOfferPrice } from "@/lib/offer-price";
import { parsePublicStorefront } from "@/lib/public-storefront";

export const dynamic = "force-dynamic";

export default async function SellerStorefront({
  params
}: {
  params: Promise<{ sellerSlug: string }>;
}) {
  const { sellerSlug } = await params;
  const storefront = await loadStorefront(sellerSlug);
  const demoCheckoutEnabled = demoModeEnabled() && storefront.seller.slug === "autonomia-demo";
  const loginHref = demoCheckoutEnabled ? "/auth/login?persona=buyer" : "/auth/login";

  return <div className="storefront-page">
    <a className="skip-link" href="#storefront-content">Pular para os produtos</a>
    <header className="storefront-topbar">
      <Link className="landing-brand" href="/" aria-label="Autonom.ia Sell — início">
        <span className="brand-mark" aria-hidden="true">a</span>
        <strong>Autonom.ia <span>Sell</span></strong>
      </Link>
      <Link className="storefront-login" href={loginHref}>Entrar</Link>
    </header>
    <main id="storefront-content" tabIndex={-1}>
      <section className="storefront-hero" aria-labelledby="storefront-title">
        <div className="storefront-identity" aria-hidden="true">
          {initials(storefront.seller.name)}
        </div>
        <div>
          <p className="eyebrow">Loja oficial na Autonom.ia Sell</p>
          <h1 id="storefront-title">{storefront.seller.name}</h1>
          <p>Produtos digitais publicados por este produtor, em um catálogo isolado e verificável.</p>
        </div>
      </section>

      <section className="storefront-trust" aria-label="Garantias do catálogo">
        <span>Catálogo publicado</span>
        <span>Acesso protegido</span>
        <span>Mobile-first</span>
      </section>

      <section className="storefront-catalog" aria-labelledby="catalog-title">
        <header>
          <p className="eyebrow">Aprenda no seu ritmo</p>
          <h2 id="catalog-title">Produtos disponíveis</h2>
        </header>
        {storefront.products.length ? <div className="storefront-grid">
          {storefront.products.map((product, index) => <article
            className="storefront-product"
            key={product.id}
            aria-labelledby={`storefront-product-${product.id}`}
          >
            <div className="storefront-product-art" aria-hidden="true">
              <span>{String(index + 1).padStart(2, "0")}</span>
              <i/>
            </div>
            <div className="storefront-product-copy">
              <p className="eyebrow">Produto digital</p>
              <h3 id={`storefront-product-${product.id}`}>{product.title}</h3>
              {product.description.trim() ? <p>{product.description}</p> : null}
              {product.price ? <p className="storefront-product-price">
                <strong>{formatOfferPrice(product.price.unit_amount_minor, product.price.currency)}</strong>
                {product.price.billing_period === "one_time" ? null : <span> · {billingPeriodLabel(product.price.billing_period)}</span>}
              </p> : null}
            </div>
            <div className="storefront-product-action">
              <Link className="primary-button" href={loginHref}>{demoCheckoutEnabled ? "Comprar com cupom demo" : "Entrar para acessar"}</Link>
              {demoCheckoutEnabled ? <small>Compra demonstrativa de valor zero, sem cobrança financeira.</small> : null}
            </div>
          </article>)}
        </div> : <div className="storefront-empty">
          <h3>Nenhum produto publicado agora</h3>
          <p>Este produtor ainda está preparando o catálogo.</p>
        </div>}
      </section>
    </main>
    <footer className="storefront-footer">
      <span>Autonom.ia Sell</span>
      <small>Infraestrutura de produtos digitais para negócios independentes.</small>
    </footer>
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

function initials(value: string) {
  return value.split(/\s+/u).slice(0, 2).map((part) => part.slice(0, 1)).join("").toUpperCase();
}
