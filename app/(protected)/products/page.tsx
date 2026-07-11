import { SellerOnboardingCard } from "../../components/seller-onboarding-card";
import { ProductDraftWorkspace } from "../../components/product-draft-workspace";
import { Suspense } from "react";

export default function ProductsPage() {
  return (
    <>
      <SellerOnboardingCard/>
      <Suspense fallback={<p className="empty-state" role="status">Carregando catálogo…</p>}><ProductDraftWorkspace/></Suspense>
    </>
  );
}
