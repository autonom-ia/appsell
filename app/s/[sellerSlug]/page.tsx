import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { cache } from "react";

import { StoreIdentity, StorefrontFooter, StorefrontTopbar, storefrontClassName, storefrontStyle } from "@/app/components/storefront-brand";
import { demoModeEnabled } from "@/lib/demo-config";
import {
  DomainApiError,
  readPublicStorefront
} from "@/lib/internal-api-client.server";
import { billingPeriodLabel, formatOfferPrice } from "@/lib/offer-price";
import { productArt } from "@/lib/product-art";
import { parsePublicStorefront } from "@/lib/public-storefront";
import { SITE } from "@/lib/site";

export const dynamic = "force-dynamic";

export async function generateMetadata({
  params
}: {
  params: Promise<{ sellerSlug: string }>;
}): Promise<Metadata> {
  const { sellerSlug } = await params;
  const { seller } = await loadStorefront(sellerSlug);
  const url = `${SITE.origem}/s/${seller.slug}`;
  const titulo = `${seller.name}, cursos online`;
  const descricao = seller.tagline ?? `Cursos de ${seller.name} para assistir pelo navegador do celular. Compra pela loja oficial na Autonom.ia Sell, com acesso no nome de quem paga.`;

  return {
    title: titulo,
    description: descricao,
    alternates: { canonical: url },
    openGraph: { type: "website", title: titulo, description: descricao, url, siteName: SITE.nome, locale: "pt_BR" }
  };
}

// A vitrine da loja (#352). A marca é a do vendedor: logo ou iniciais, a
// frase dele, a cor dele nos botões. O texto da plataforma sai da frente e vai
// para o rodapé.
export default async function SellerStorefront({
  params
}: {
  params: Promise<{ sellerSlug: string }>;
}) {
  const { sellerSlug } = await params;
  const storefront = await loadStorefront(sellerSlug);
  const seller = storefront.seller;
  const demoCheckoutEnabled = demoModeEnabled() && seller.slug === "autonomia-demo";
  const loginHref = demoCheckoutEnabled ? "/auth/login?persona=buyer" : "/auth/login";

  return <div className={storefrontClassName(seller)} style={storefrontStyle(seller)}>
    <a className="skip-link" href="#storefront-content">Pular para os produtos</a>
    <StorefrontTopbar seller={seller} action={<Link className="storefront-login" href={loginHref}>Entrar</Link>}/>
    <main id="storefront-content" tabIndex={-1}>
      <section className="storefront-hero" aria-labelledby="storefront-title">
        <StoreIdentity seller={seller} size="large"/>
        <div>
          <h1 id="storefront-title">{seller.name}</h1>
          <p>{seller.tagline ?? `Cursos e materiais de ${seller.name}, para assistir pelo navegador, no celular ou no computador.`}</p>
        </div>
      </section>

      <section className="storefront-catalog" aria-labelledby="catalog-title">
        <header>
          <p className="eyebrow">Aprenda no seu ritmo</p>
          <h2 id="catalog-title">Produtos disponíveis</h2>
        </header>
        {storefront.products.length ? <div className="storefront-grid">
          {storefront.products.map((product) => {
            const art = productArt(product.title);
            return <article
              className="storefront-product"
              key={product.id}
              aria-labelledby={`storefront-product-${product.id}`}
            >
              <div className="storefront-product-art" aria-hidden="true" style={product.cover ? undefined : { background: art.gradient }}>
                {product.cover
                  // eslint-disable-next-line @next/next/no-img-element
                  ? <img className="storefront-product-cover" src={product.cover.url} alt="" width={product.cover.width} height={product.cover.height} decoding="async" loading="lazy"/>
                  : <span>{art.initials}</span>}
              </div>
              <div className="storefront-product-copy">
                <p className="eyebrow">{product.curriculum ? `${product.curriculum.lesson_count} ${product.curriculum.lesson_count === 1 ? "aula" : "aulas"}` : "Produto digital"}</p>
                <h3 id={`storefront-product-${product.id}`}>{product.title}</h3>
                {product.headline ? <p>{product.headline}</p> : product.description.trim() ? <p>{product.description}</p> : null}
                {product.price ? <p className="storefront-product-price">
                  <strong>{formatOfferPrice(product.price.unit_amount_minor, product.price.currency)}</strong>
                  {product.price.billing_period === "one_time" ? null : <span> · {billingPeriodLabel(product.price.billing_period)}</span>}
                </p> : null}
              </div>
              <div className="storefront-product-action">
                <Link className={demoCheckoutEnabled || (product.access_mode ?? "paid") === "paid" ? "primary-button commercial-button" : "primary-button"} href={demoCheckoutEnabled ? loginHref : (product.access_mode ?? "paid") === "free" ? `/s/${seller.slug}/p/${product.slug}/access` : `/s/${seller.slug}/p/${product.slug}`}>{demoCheckoutEnabled ? "Comprar com cupom demo" : (product.access_mode ?? "paid") === "paid" ? "Ver e comprar" : "Acessar gratuitamente"}</Link>
                {demoCheckoutEnabled ? <small>Compra demonstrativa de valor zero, sem cobrança financeira.</small> : null}
              </div>
            </article>;
          })}
        </div> : <div className="storefront-empty">
          <h3>Nenhum produto publicado agora</h3>
          <p>{seller.name} ainda está preparando o catálogo.</p>
        </div>}
      </section>
    </main>
    <StorefrontFooter/>
  </div>;
}

const loadStorefront = cache(async function loadStorefront(sellerSlug: string) {
  try {
    const parsed = parsePublicStorefront(await readPublicStorefront(sellerSlug));
    if (!parsed) throw new DomainApiError(503, "invalid_public_storefront");
    return parsed;
  } catch (error) {
    if (error instanceof DomainApiError && error.status === 404) notFound();
    throw error;
  }
});
