import type { CatalogProduct } from "../../lib/catalog-projection";

interface ProductCardProps {
  product: CatalogProduct;
  progressPercent?: number;
  compact?: boolean;
}
export function ProductCard({ product, progressPercent, compact = false }: ProductCardProps) {
  const price = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(product.priceInCents / 100);
  return (
    <article className={`product-card accent-${product.accent}${compact ? " compact" : ""}`}>
      <div className="product-art" aria-hidden="true"><span>{product.title.slice(0, 2).toUpperCase()}</span></div>
      <div className="product-body">
        <div className="product-meta"><span>{product.sellerName}</span><span className={`status status-${product.status}`}>{product.status === "published" ? "Publicado" : product.status === "draft" ? "Rascunho" : "Arquivado"}</span></div>
        <h3>{product.title}</h3>
        <p>{product.description}</p>
        {progressPercent === undefined ? (
          <div className="product-footer"><strong>{product.priceInCents ? price : "Preço pendente"}</strong><span>{product.lessonCount} aulas</span></div>
        ) : (
          <div className="progress-block" aria-label={`${progressPercent}% concluído`}><div><span>Seu progresso</span><strong>{progressPercent}%</strong></div><div className="progress-track"><span style={{ width: `${progressPercent}%` }} /></div></div>
        )}
      </div>
    </article>
  );
}
