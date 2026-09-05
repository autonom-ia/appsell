import type { Metadata } from "next";
import { cookies } from "next/headers";
import { cache } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";

import { StoreIdentity, StorefrontFooter, StorefrontTopbar, storefrontClassName, storefrontStyle } from "@/app/components/storefront-brand";
import { StripeCheckoutCard } from "@/app/components/stripe-checkout-card";
import { productCheckoutLoginHref } from "@/lib/auth-navigation";
import { sessionCookieName } from "@/lib/cookies";
import {
  DomainApiError,
  readPublicStorefront
} from "@/lib/internal-api-client.server";
import { oauthService } from "@/lib/oauth-service.server";
import { isProductCheckoutAvailable } from "@/lib/product-checkout-availability.server";
import { parsePublicStorefront } from "@/lib/public-storefront";
import { productCardImage, twitterCardKind } from "@/lib/social-card";
import { SITE } from "@/lib/site";

export const dynamic = "force-dynamic";

// O link do produto é o que mais circula, e vinha sem metadata nenhuma: o card
// dizia "Autonom.ia Sell" e a descrição da plataforma, nem o nome do produto
// (#412). Título e descrição são do produto; a imagem segue a ordem da página,
// banner do produto, capa, e só então a marca da loja.
export async function generateMetadata({
  params
}: {
  params: Promise<{ sellerSlug: string; productSlug: string }>;
}): Promise<Metadata> {
  const { sellerSlug, productSlug } = await params;
  const { seller, products } = await loadStorefront(sellerSlug);
  const product = products.find((item) => item.slug === productSlug);
  if (!product) return { title: seller.name };

  const url = `${SITE.origem}/s/${seller.slug}/p/${product.slug}`;
  const titulo = `${product.title}, por ${seller.name}`;
  const descricao = product.headline ?? product.description;
  const card = productCardImage(seller, product);
  const images = card ? [card] : undefined;

  return {
    title: titulo,
    description: descricao,
    alternates: { canonical: url },
    openGraph: { type: "website", title: titulo, description: descricao, url, siteName: SITE.nome, locale: "pt_BR", images },
    twitter: { card: twitterCardKind(card), title: titulo, description: descricao, images }
  };
}

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
  const loginParaComprar = productCheckoutLoginHref(productPath);
  // Quem ainda não entrou não precisa de um cartão no meio do caminho dizendo
  // "entre para comprar": o botão já diz o que faz e leva direto ao login.
  const mostraCartaoDeCompra = accessMode === "free" || !checkoutAvailable || Boolean(buyer);
  const ctaLabel = accessMode === "free" ? "Acessar gratuitamente" : "Comprar agora";
  const ctaHref = accessMode === "free"
    ? accessPath
    : mostraCartaoDeCompra ? "#comprar" : loginParaComprar;
  const showCta = accessMode === "free" || checkoutAvailable;

  return <div className={storefrontClassName(seller, "product-checkout-page")} style={storefrontStyle(seller)}>
    <a className="skip-link" href="#product-checkout-content">Pular para o produto</a>
    <StorefrontTopbar seller={seller} action={<Link className="storefront-login" href="/auth/login">Já sou aluno</Link>}/>
    <main id="product-checkout-content" tabIndex={-1} className="product-checkout-main lp-main">
      <section className="product-checkout-summary lp-hero" aria-labelledby="product-checkout-title">
        {/* O banner, e só ele: a capa é 16:9 e vive no cartão da loja. Espremer
            a capa nesta faixa foi o defeito que trouxe o banner (#387). Sem
            banner, a página abre pela promessa, sem imagem cortada. */}
        {/* Uma imagem no alto, nunca duas. O banner é a arte feita para esta
            página; a capa entra no lugar dele quando o produto ainda não tem
            banner, para nenhum produto abrir sem imagem (#398). Empilhar as
            duas mostra a mesma arte em dois formatos e empurra preço e botão
            para baixo da dobra. */}
        {product.banner
          // eslint-disable-next-line @next/next/no-img-element
          ? <div className="lp-banner"><img className="lp-banner-image" src={product.banner.url} alt="" width={product.banner.width} height={product.banner.height} decoding="async" fetchPriority="high"/></div>
          : product.cover
          // eslint-disable-next-line @next/next/no-img-element
          ? <img
              className="lp-cover"
              src={`/api/storefronts/${encodeURIComponent(seller.slug)}/products/${encodeURIComponent(product.slug)}/cover`}
              alt=""
              width={product.cover.width}
              height={product.cover.height}
              decoding="async"
            />
          : null}
        <p className="eyebrow">{seller.name}</p>
        <h1 id="product-checkout-title">{product.headline ?? product.title}</h1>
        {product.headline ? <p className="lp-title">{product.title}</p> : null}
        {product.description.trim() ? <p>{product.description}</p> : null}
        <p className="product-checkout-price">{priceLabel}</p>
        {showCta ? <Link className={accessMode === "paid" ? "primary-button commercial-button lp-cta" : "primary-button lp-cta"} href={ctaHref}>{ctaLabel}</Link> : null}
        <ul className="lp-trust" role="list" aria-label="O que você pode esperar">
          <li>{accessMode === "paid" ? "Acesso imediato depois da compra" : "Acesso imediato"}</li>
          <li>Assista no celular ou no computador</li>
          {accessMode === "paid" ? <li>Pagamento seguro</li> : null}
        </ul>
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

      {mostraCartaoDeCompra ? <section id="comprar" className="product-checkout-action" aria-label={accessMode === "free" ? "Liberar acesso" : "Finalizar compra"}>
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
              <Link className="primary-button commercial-button" href={loginParaComprar}>Entrar ou criar conta</Link>
            </div>
          : <div className="checkout-card checkout-unavailable" role="status">
              <h2>As vendas abrem em breve</h2>
              <p>{seller.name} ainda está ativando os pagamentos desta loja. Volte em breve para comprar este produto.</p>
            </div>}
      </section> : null}
    </main>
    {accessMode === "paid" && checkoutAvailable ? <div className="lp-sticky" aria-hidden={mostraCartaoDeCompra}>
      <strong>{priceLabel}</strong>
      {/* Enquanto ela apenas repete o cartão logo abaixo, é decoração e sai do
          caminho do leitor de tela. Quando é o único caminho para a compra,
          precisa ser alcançável pelo teclado. */}
      <a
        className="primary-button commercial-button"
        href={ctaHref}
        tabIndex={mostraCartaoDeCompra ? -1 : undefined}
      >Comprar agora</a>
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

// `cache` porque a página e o `generateMetadata` pedem a mesma vitrine na
// mesma requisição: sem isso, todo acesso bateria duas vezes no BFF.
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

function formatPrice(unitAmountMinor: number, currency: string): string {
  return new Intl.NumberFormat("pt-BR", { style: "currency", currency }).format(unitAmountMinor / 100);
}
