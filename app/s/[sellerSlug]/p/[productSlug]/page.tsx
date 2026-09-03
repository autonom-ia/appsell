import { cookies } from "next/headers";
import Link from "next/link";
import { notFound } from "next/navigation";

import { StoreIdentity, StorefrontCover, StorefrontFooter, StorefrontTopbar, storefrontClassName, storefrontStyle } from "@/app/components/storefront-brand";
import { StripeCheckoutCard } from "@/app/components/stripe-checkout-card";
import { sessionCookieName } from "@/lib/cookies";
import {
  DomainApiError,
  readPublicStorefront
} from "@/lib/internal-api-client.server";
import { oauthService } from "@/lib/oauth-service.server";
import { isProductCheckoutAvailable } from "@/lib/product-checkout-availability.server";
import { parsePublicStorefront } from "@/lib/public-storefront";

export const dynamic = "force-dynamic";

// A página de vendas do produto (#352). Cinco blocos, nesta ordem: capa e
// promessa, o que a pessoa recebe (lido da versão publicada), quem ensina,
// preço com o botão, e o botão repetido fixo no celular. O que o vendedor não
// preencheu não aparece — nunca um bloco vazio.
export default async function ProductCheckoutPage({
  params
}: {
  params: Promise<{ sellerSlug: string; productSlug: string }>;
}) {
  const { sellerSlug, productSlug } = await params;
  const storefront = await loadStorefront(sellerSlug);
  const seller = storefront.seller;
  const product = storefront.products.find((item) => item.slug === productSlug);
  if (!product) notFound();
  const accessMode = product.access_mode ?? "paid";
  if (accessMode === "paid" && !product.price) notFound();

  const checkoutAvailable = accessMode === "paid" &&
    isProductCheckoutAvailable(sellerSlug, productSlug);
  const buyer = checkoutAvailable ? await authenticatedBuyer() : null;
  const priceLabel = product.price ? formatPrice(product.price.unit_amount_minor, product.price.currency) : "Acesso gratuito";
  const accessPath = `/s/${seller.slug}/p/${product.slug}/access`;
  const productPath = `/s/${seller.slug}/p/${product.slug}`;
  const curriculum = product.curriculum;
  const hasAuthor = Boolean(seller.about || seller.logo);
  const ctaLabel = accessMode === "free" ? "Acessar gratuitamente" : "Comprar agora";
  const ctaHref = accessMode === "free" ? accessPath : "#comprar";
  const showCta = accessMode === "free" || checkoutAvailable;

  return <div className={storefrontClassName(seller, "product-checkout-page")} style={storefrontStyle(seller)}>
    <a className="skip-link" href="#product-checkout-content">Pular para o produto</a>
    <StorefrontTopbar seller={seller} action={<Link className="storefront-login" href="/auth/login">Entrar</Link>}/>
    {seller.banner ? <div className="lp-banner"><StorefrontCover seller={seller}/></div> : null}
    <main id="product-checkout-content" tabIndex={-1} className="product-checkout-main lp-main">
      <section className="product-checkout-summary lp-hero" aria-labelledby="product-checkout-title">
        {product.cover
          // eslint-disable-next-line @next/next/no-img-element
          ? <img className="lp-cover" src={product.cover.url} alt="" width={product.cover.width} height={product.cover.height} decoding="async"/>
          : null}
        <p className="eyebrow">{seller.name}</p>
        <h1 id="product-checkout-title">{product.headline ?? product.title}</h1>
        {product.headline ? <p className="lp-title">{product.title}</p> : null}
        {product.description.trim() ? <p>{product.description}</p> : null}
        <p className="product-checkout-price">{priceLabel}</p>
        {showCta ? <Link className={accessMode === "paid" ? "primary-button commercial-button lp-cta" : "primary-button lp-cta"} href={ctaHref}>{ctaLabel}</Link> : null}
      </section>

      {curriculum ? <section className="lp-block" aria-labelledby="lp-contents-title">
        <p className="eyebrow">O que você vai receber</p>
        <h2 id="lp-contents-title">
          {curriculum.lesson_count} {curriculum.lesson_count === 1 ? "aula" : "aulas"}
          {curriculum.material_count > 0 ? ` · ${curriculum.material_count} ${curriculum.material_count === 1 ? "material para baixar" : "materiais para baixar"}` : ""}
        </h2>
        <ol className="lp-lessons">
          {curriculum.lessons.map((title, index) => <li key={`${index}-${title}`}>{title}</li>)}
        </ol>
        {curriculum.lesson_count > curriculum.lessons.length ? <p className="form-note">E mais {curriculum.lesson_count - curriculum.lessons.length} aulas.</p> : null}
      </section> : null}

      {hasAuthor ? <section className="lp-block lp-author" aria-labelledby="lp-author-title">
        <StoreIdentity seller={seller}/>
        <div>
          <p className="eyebrow">Sobre quem ensina</p>
          <h2 id="lp-author-title">{seller.name}</h2>
          {seller.about ? <p>{seller.about}</p> : null}
        </div>
      </section> : null}

      <section id="comprar" className="product-checkout-action" aria-label={accessMode === "free" ? "Liberar acesso" : "Finalizar compra"}>
        {accessMode === "free"
          ? <div className="checkout-card checkout-unavailable">
              <h2>Acesso gratuito</h2>
              <p>Entre ou crie sua conta para receber acesso ao produto, sem cobrança.</p>
              <Link className="primary-button" href={accessPath}>Acessar gratuitamente</Link>
            </div>
          : checkoutAvailable && buyer
          ? <StripeCheckoutCard
              sellerSlug={seller.slug}
              productSlug={product.slug}
              sellerName={seller.name}
              productTitle={product.title}
              priceLabel={priceLabel}
              accountEmail={buyer.email}
            />
          : checkoutAvailable
          ? <div className="checkout-card checkout-unavailable">
              <h2>Entre para comprar</h2>
              <p>O acesso ficará vinculado à conta usada no pagamento.</p>
              <Link className="primary-button commercial-button" href={`/auth/login?next=${encodeURIComponent(productPath)}`}>Entrar ou criar conta</Link>
            </div>
          : <div className="checkout-card checkout-unavailable" role="status">
              <h2>As vendas abrem em breve</h2>
              <p>{seller.name} ainda está ativando os pagamentos desta loja. Volte em breve para comprar este produto.</p>
            </div>}
      </section>
    </main>
    {accessMode === "paid" && checkoutAvailable ? <div className="lp-sticky" aria-hidden="true">
      <strong>{priceLabel}</strong>
      <a className="primary-button commercial-button" href="#comprar" tabIndex={-1}>Comprar agora</a>
    </div> : null}
    <StorefrontFooter/>
  </div>;
}

async function authenticatedBuyer(): Promise<{ email: string } | null> {
  try {
    const sessionId = (await cookies()).get(sessionCookieName())?.value;
    if (!sessionId) return null;
    const session = await oauthService().getSession(sessionId);
    if (!session?.subject || session.identity?.emailVerified !== true) return null;
    const email = session.identity.email;
    return typeof email === "string" && email.trim() ? { email: email.trim().toLowerCase() } : null;
  } catch {
    return null;
  }
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
