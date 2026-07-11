import { Suspense } from "react";
import { ProductEditorWorkspace } from "../../../../components/product-editor-workspace";

export default async function ProductEditorPage({ params }: { params: Promise<{ sellerId: string; productId: string }> }) {
  const { sellerId, productId } = await params;
  return (
    <Suspense fallback={<p className="empty-state" role="status">Carregando editor…</p>}>
      <ProductEditorWorkspace sellerId={sellerId} productId={productId}/>
    </Suspense>
  );
}
