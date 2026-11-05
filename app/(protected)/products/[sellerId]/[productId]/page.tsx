import { Suspense } from "react";
import { ProductEditorWorkspace } from "../../../../components/product-editor-workspace";
import { configuredCheckoutProvider } from "@/lib/financial-external-checkout.server";

export default async function ProductEditorPage({ params }: { params: Promise<{ sellerId: string; productId: string }> }) {
  const { sellerId, productId } = await params;
  return (
    <Suspense fallback={<p className="empty-state" role="status">Carregando editor…</p>}>
      <ProductEditorWorkspace
        sellerId={sellerId}
        productId={productId}
        // Sem provedor válido (#544) o editor segue o contrato do checkout
        // local, que exige conta de recebimento para publicar pago.
        usesFinancialCheckout={configuredCheckoutProvider() === "financial_checkout"}
      />
    </Suspense>
  );
}
