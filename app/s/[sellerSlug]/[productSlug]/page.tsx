import { DomainApiError } from "@/lib/internal-api-client.server";
import { readPublicProduct } from "@/lib/public-storefront.server";
import { isPublicSlug } from "@/lib/public-storefront";
import { demoModeEnabled } from "@/lib/demo-config";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import type { Metadata } from "next";
import { cache } from "react";

export const dynamic = "force-dynamic";
export const revalidate = 0;
const loadProduct = cache((sellerSlug: string, productSlug: string) =>
  readPublicProduct(sellerSlug, productSlug)
);

export async function generateMetadata({
  params
}: {
  params: Promise<{ sellerSlug: string; productSlug: string }>;
}): Promise<Metadata> {
  const { sellerSlug, productSlug } = await params;
  if (!isPublicSlug(sellerSlug) || !isPublicSlug(productSlug)) {
    return { title: "Produto não encontrado", robots: { index: false } };
  }
  try {
    const payload = await loadProduct(sellerSlug, productSlug);
    const title = `${payload.product.title} | ${payload.seller.name}`;
    const description = (payload.product.description || payload.product.offer.summary ||
      `Conheça ${payload.product.title}.`).slice(0, 200);
    const url = `/s/${payload.seller.slug}/${payload.product.slug}`;
    return {
      title,
      description,
      alternates: { canonical: url },
      openGraph: { title, description, type: "website", url }
    };
  } catch {
    return { title: "Produto indisponível", robots: { index: false } };
  }
}

export default async function PublicProductPage({
  params
}: {
  params: Promise<{ sellerSlug: string; productSlug: string }>;
}) {
  const { sellerSlug, productSlug } = await params;
  if (!isPublicSlug(sellerSlug) || !isPublicSlug(productSlug)) notFound();
  let payload;
  try {
    payload = await loadProduct(sellerSlug, productSlug);
  } catch (error) {
    if (error instanceof DomainApiError && error.status === 404) notFound();
    if (error instanceof DomainApiError && error.status >= 500) {
      redirect(`/storefront-unavailable?seller=${sellerSlug}&product=${productSlug}`);
    }
    throw error;
  }
  const { seller, product, other_products: others } = payload;
  const demoEnabled = demoModeEnabled();

  return <main id="conteudo-vitrine" tabIndex={-1}>
    <nav className="storefront-breadcrumb" aria-label="Navegação estrutural">
      <Link prefetch={false} href={`/s/${seller.slug}`}>← Vitrine de {seller.name}</Link>
    </nav>
    <section className="storefront-product-hero">
      <div className="storefront-product-copy">
        <p className="eyebrow">Produto publicado</p>
        <h1>{product.title}</h1>
        {product.description ? <p className="storefront-product-description">{product.description}</p> : null}
        <div className="storefront-product-actions">
          <Link className="primary-button" href={demoEnabled ? "/auth/login?persona=buyer" : "/auth/login"}>{demoEnabled ? "Entrar como aluno demo" : "Já tenho acesso"} <span aria-hidden="true">→</span></Link>
          <Link prefetch={false} className="secondary-button" href={`/s/${seller.slug}`}>Ver catálogo</Link>
        </div>
      </div>
      <aside className="storefront-offer" aria-labelledby="offer-title">
        <p className="eyebrow">Sobre o acesso</p>
        <h2 id="offer-title">{product.offer.title}</h2>
        {product.offer.summary ? <p>{product.offer.summary}</p> : <p>Entre para acessar este produto caso sua inscrição já esteja ativa.</p>}
        <span>Se você já recebeu acesso, entre com a conta usada na inscrição.</span>
      </aside>
    </section>

    <section className="storefront-curriculum" aria-labelledby="curriculum-title">
      <div className="storefront-section-heading"><div><p className="eyebrow">Conteúdo</p><h2 id="curriculum-title">O que você vai encontrar</h2></div></div>
      {product.courses.length ? <ol>
        {product.courses.map((course, index) => <li key={`${course.position}-${course.title}`}>
          <span aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
          <div><h3>{course.title}</h3>{course.description ? <p>{course.description}</p> : null}</div>
        </li>)}
      </ol> : <p className="storefront-empty-content">O produtor ainda não publicou o conteúdo programático deste produto.</p>}
    </section>

    {others.length ? <section className="storefront-others" aria-labelledby="others-title">
      <div className="storefront-section-heading"><div><p className="eyebrow">Mesmo produtor</p><h2 id="others-title">Outros produtos de {seller.name}</h2></div></div>
      <div className="storefront-other-grid">{others.map((other) => <article key={other.slug}>
        <p>{other.course_count} {other.course_count === 1 ? "curso" : "cursos"}</p>
        <h3>{other.title}</h3>
        {other.description ? <p>{other.description}</p> : null}
        <Link prefetch={false} href={`/s/${seller.slug}/${other.slug}`}>Conhecer produto <span aria-hidden="true">→</span></Link>
      </article>)}</div>
    </section> : null}
  </main>;
}
