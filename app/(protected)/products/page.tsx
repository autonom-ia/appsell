import Link from "next/link";
import { ProductCard } from "../../components/product-card";
import { DEMO_SELLER_ID, demoProducts } from "../../../lib/demo-catalog";
import { listSellerProducts } from "../../../lib/catalog-projection";
import { SellerOnboardingCard } from "../../components/seller-onboarding-card";

interface ProductsPageProps {
  searchParams: Promise<{ status?: string }>;
}
export default async function ProductsPage({ searchParams }: ProductsPageProps) {
  const products = listSellerProducts(demoProducts, DEMO_SELLER_ID);
  const { status } = await searchParams;
  const activeStatus = status === "published" || status === "draft" ? status : "all";
  const visibleProducts = activeStatus === "all" ? products : products.filter((product) => product.status === activeStatus);
  const filters = [
    { key: "all", label: "Todos", href: "/products", count: products.length },
    { key: "published", label: "Publicados", href: "/products?status=published", count: products.filter((product) => product.status === "published").length },
    { key: "draft", label: "Rascunhos", href: "/products?status=draft", count: products.filter((product) => product.status === "draft").length },
  ];

  return (
    <>
      <SellerOnboardingCard/>
      <header className="page-header">
        <div><p className="eyebrow">Catálogo demonstrativo</p><h1>Produtos</h1><p>Prévia da separação entre rascunhos e produtos publicados.</p></div>
        <div className="blocked-action">
          <button className="primary-button" type="button" aria-disabled="true" aria-describedby="products-create-help">Criar produto</button>
          <p id="products-create-help">Disponível após migrations, RLS e RBAC.</p>
        </div>
      </header>
      <nav className="filter-row" aria-label="Filtros do catálogo">{filters.map((filter) => <Link key={filter.key} href={filter.href} className={activeStatus === filter.key ? "filter active" : "filter"} aria-current={activeStatus === filter.key ? "page" : undefined}>{filter.label} <span>{filter.count}</span></Link>)}</nav>
      {visibleProducts.length ? <div className="product-grid product-grid-wide">{visibleProducts.map((product) => <ProductCard key={product.id} product={product}/>)}</div> : <p className="empty-state">Nenhum produto neste filtro. Escolha outro status.</p>}
    </>
  );
}
