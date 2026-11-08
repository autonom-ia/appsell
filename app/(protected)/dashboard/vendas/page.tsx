import { DashboardSalesReport } from "../../../components/dashboard-sales-report";

export const dynamic = "force-dynamic";

// Relatório de vendas da loja (#562 v1): quantidade de vendas e estornos por
// dia e produto, e o CSV. Em piloto por loja; fora dele a página diz que não
// está disponível.
export default function SalesReportPage() {
  return (
    <>
      <header className="page-header">
        <div>
          <p className="eyebrow">Relatórios</p>
          <h1>Vendas</h1>
          <p>Vendas e estornos por dia e produto, contados pelos avisos de pagamento.</p>
        </div>
      </header>
      <DashboardSalesReport/>
    </>
  );
}
