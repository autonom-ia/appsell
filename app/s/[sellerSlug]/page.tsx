import { DomainApiError } from "@/lib/internal-api-client.server";
import { readPublicStorefront } from "@/lib/public-storefront.server";
import { isPublicSlug } from "@/lib/public-storefront";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import type { Metadata } from "next";
import { cache } from "react";

export const dynamic = "force-dynamic";
export const revalidate = 0;
const loadStorefront = cache((sellerSlug: string, cursor: string | null, limit: number) =>
  readPublicStorefront(sellerSlug, cursor, limit)
);

export async function generateMetadata({
  params
}: {
  params: Promise<{ sellerSlug: string }>;
}): Promise<Metadata> {
  const { sellerSlug } = await params;
  if (!isPublicSlug(sellerSlug)) return { title: "Vitrine não encontrada", robots: { index: false } };
  try {
    const storefront = await loadStorefront(sellerSlug, null, 12);
    const title = `${storefront.seller.name} | Autonom.ia Sell`;
    const description = `Conheça os produtos digitais publicados por ${storefront.seller.name}.`;
    const url = `/s/${storefront.seller.slug}`;
    return {
      title,
      description,
      alternates: { canonical: url },
      openGraph: { title, description, type: "website", url }
    };
  } catch {
    return { title: "Vitrine indisponível", robots: { index: false } };
  }
}

export default async function StorefrontPage({
  params,
  searchParams
}: {
  params: Promise<{ sellerSlug: string }>;
  searchParams: Promise<{ cursor?: string | string[] }>;
}) {
  const { sellerSlug } = await params;
  const rawCursor = (await searchParams).cursor;
  const cursor = typeof rawCursor === "string" ? rawCursor : null;
  if (!isPublicSlug(sellerSlug) || (rawCursor !== undefined && !isPublicSlug(cursor))) notFound();
  let storefront;
  try {
    storefront = await loadStorefront(sellerSlug, cursor, 12);
  } catch (error) {
    if (error instanceof DomainApiError && error.status === 404) notFound();
    if (error instanceof DomainApiError && error.status >= 500) {
      redirect(`/storefront-unavailable?seller=${sellerSlug}`);
    }
    throw error;
  }

  return <main id="conteudo-vitrine" tabIndex={-1}>
    <section className="storefront-hero" aria-labelledby="storefront-title">
      <div>
        <p className="eyebrow">Vitrine do produtor</p>
        <h1 id="storefront-title">{storefront.seller.name}</h1>
        <p>Explore os produtos publicados por este produtor. Cada vitrine é independente e preserva seu próprio catálogo.</p>
      </div>
      <span className="storefront-count">{storefront.products.length} {storefront.products.length === 1 ? "produto nesta página" : "produtos nesta página"}</span>
    </section>

    <section className="storefront-catalog" aria-labelledby="catalog-title">
      <div className="storefront-section-heading">
        <div><p className="eyebrow">Catálogo público</p><h2 id="catalog-title">Escolha por onde começar</h2></div>
      </div>
      <div className="storefront-grid">
        {storefront.products.map((product, index) => <PublicProductCard
          key={product.slug}
          sellerSlug={storefront.seller.slug}
          product={product}
          accent={index % 3}
        />)}
      </div>
      {cursor || storefront.next_cursor ? <nav className="storefront-pagination" aria-label="Paginação do catálogo">
        {cursor ? <Link prefetch={false} className="secondary-button" href={`/s/${storefront.seller.slug}`}>Voltar ao início</Link> : null}
        {storefront.next_cursor ? <Link prefetch={false} className="primary-button" href={{
          pathname: `/s/${storefront.seller.slug}`,
          query: { cursor: storefront.next_cursor }
        }}>Ver mais produtos <span aria-hidden="true">→</span></Link> : null}
      </nav> : null}
    </section>
  </main>;
}

function PublicProductCard({
  sellerSlug,
  product,
  accent
}: {
  sellerSlug: string;
  product: {
    slug: string;
    title: string;
    description: string;
    course_count: number;
    offer: { title: string; summary: string };
  };
  accent: number;
}) {
  return <article className={`storefront-card storefront-accent-${accent}`}>
    <div className="storefront-card-art" aria-hidden="true"><span>{product.title.slice(0, 1).toUpperCase()}</span></div>
    <div className="storefront-card-body">
      <p className="storefront-card-meta">{product.course_count} {product.course_count === 1 ? "curso" : "cursos"}</p>
      <h3>{product.title}</h3>
      <p>{product.description || product.offer.summary || "Conheça os detalhes deste produto."}</p>
      <Link prefetch={false} className="storefront-card-link" href={`/s/${sellerSlug}/${product.slug}`}>
        Conhecer produto <span aria-hidden="true">→</span>
      </Link>
    </div>
  </article>;
}
