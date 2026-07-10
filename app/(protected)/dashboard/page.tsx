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
        <div><p className="eyebrow">Prévia do produtor</p><h1>Bom dia, creator.</h1><p>Veja como o seu negócio poderá ser acompanhado.</p></div>
        <div className="blocked-action">
          <button className="primary-button" type="button" aria-disabled="true" aria-describedby="dashboard-create-help">Criar produto</button>
          <p id="dashboard-create-help">Disponível após o gate multitenant.</p>
        </div>
      </header>
      <section className="metric-grid" aria-label="Resumo demonstrativo do negócio">
        <article><span>Produtos</span><strong>{String(products.length).padStart(2, "0")}</strong><small>{published} publicados</small></article>
        <article><span>Alunos</span><strong>128</strong><small>Dados demonstrativos</small></article>
        <article><span>Receita</span><strong>—</strong><small>Financial não conectado</small></article>
      </section>
      <section className="section-heading"><div><p className="eyebrow">Catálogo demonstrativo</p><h2>Seus produtos</h2></div><Link href="/products">Ver todos <span aria-hidden="true">→</span></Link></section>
      {products.length ? <div className="product-grid">{products.slice(0, 2).map((product) => <ProductCard key={product.id} product={product}/>)}</div> : <p className="empty-state">Nenhum produto demonstrativo disponível.</p>}
      <section className="blocked-card"><span className="blocked-icon" aria-hidden="true">!</span><div><strong>Próximo gate: foundation multitenant</strong><p>Criação, edição e publicação serão liberadas apenas depois de migrations, RLS e RBAC passarem nos testes cross-tenant.</p></div></section>
    </>
  );
}
