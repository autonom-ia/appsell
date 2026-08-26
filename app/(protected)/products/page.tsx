import { SellerOnboardingCard } from "../../components/seller-onboarding-card";
import { ProductDraftWorkspace } from "../../components/product-draft-workspace";
import { Suspense } from "react";

export default function ProductsPage() {
  return (
    <>
      <header className="page-header products-page-header"><div><p className="eyebrow">Gestão do catálogo</p><h1>Produtos</h1><p>Crie, organize e publique seus produtos na loja que você escolher.</p></div></header>
      <SellerOnboardingCard hideSingleSpaceSummary/>
      <Suspense fallback={<p className="empty-state" role="status">Carregando catálogo…</p>}><ProductDraftWorkspace/></Suspense>
    </>
  );
}
