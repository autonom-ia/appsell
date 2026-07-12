import Link from "next/link";
import { ProductCard } from "../../components/product-card";
import { DEMO_SELLER_ID, demoProducts } from "../../../lib/demo-catalog";
import { listSellerProducts } from "../../../lib/catalog-projection";

export default function Dashboard() {
  const products = listSellerProducts(demoProducts, DEMO_SELLER_ID);
  const published = products.filter((product) => product.status === "published").length;

  return (
    <>
      <header className="page-header">
        <div><p className="eyebrow">Visão geral</p><h1>Seu negócio em um só lugar.</h1><p>Acompanhe o catálogo e continue de onde parou.</p></div>
        <Link className="primary-button" href="/products">Criar produto</Link>
      </header>
      <section className="metric-grid" aria-label="Resumo demonstrativo do negócio">
        <article><span>Produtos</span><strong>{String(products.length).padStart(2, "0")}</strong><small>{published} publicados</small></article>
        <article><span>Alunos</span><strong>128</strong><small>Dados demonstrativos</small></article>
        <article><span>Receita</span><strong>—</strong><small>Financeiro ainda não conectado</small></article>
      </section>
      <section className="section-heading"><div><p className="eyebrow">Catálogo demonstrativo</p><h2>Seus produtos</h2></div><Link href="/products">Ver todos <span aria-hidden="true">→</span></Link></section>
      {products.length ? <div className="product-grid">{products.slice(0, 2).map((product) => <ProductCard key={product.id} product={product}/>)}</div> : <p className="empty-state">Nenhum produto demonstrativo disponível.</p>}
      <section className="blocked-card"><span className="blocked-icon" aria-hidden="true">i</span><div><strong>Ambiente de demonstração</strong><p>Pagamentos e login comercial ainda estão desativados. Criação, edição, currículo e publicação local já podem ser demonstrados com isolamento entre espaços.</p></div></section>
    </>
  );
}
