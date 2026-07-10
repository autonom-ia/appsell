import { ProductCard } from "../../components/product-card";
import { DEMO_BUYER_SUB, demoEntitlements, demoProducts } from "../../../lib/demo-catalog";
import { listBuyerProducts, listOtherPublishedProductsFromSeller } from "../../../lib/catalog-projection";

export default function LibraryPage() {
  const library = listBuyerProducts(demoProducts, demoEntitlements, DEMO_BUYER_SUB);
  const selected = library[0];
  const otherProducts = selected ? listOtherPublishedProductsFromSeller(demoProducts, selected) : [];

  return (
    <>
      <header className="page-header"><div><p className="eyebrow">Prévia do comprador</p><h1>Minha biblioteca</h1><p>Exemplo de produtos ativos de diferentes produtores, sem grants reais.</p></div></header>
      {library.length ? <div className="product-grid">{library.map((product) => <ProductCard key={`${product.sellerOrganizationId}:${product.id}`} product={product} progressPercent={product.progressPercent}/>)}</div> : <p className="empty-state">Nenhum produto demonstrativo nesta biblioteca.</p>}
      {selected ? <section className="recommendations"><div className="section-heading"><div><p className="eyebrow">Mesmo produtor</p><h2>Outros produtos de {selected.sellerName}</h2></div></div>{otherProducts.length ? <div className="product-grid">{otherProducts.map((product) => <ProductCard key={product.id} product={product} compact/>)}</div> : <p className="empty-state">Nenhum outro produto publicado por este produtor.</p>}</section> : null}
    </>
  );
}
