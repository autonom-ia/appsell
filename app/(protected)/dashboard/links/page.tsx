import { DashboardCampaignLinks } from "../../../components/dashboard-campaign-links";

export const dynamic = "force-dynamic";

// Links UTM da loja e vendas por campanha (#563). Em piloto por loja; fora
// dele a página diz que não está disponível.
export default function CampaignLinksPage() {
  return (
    <>
      <header className="page-header">
        <div>
          <p className="eyebrow">Divulgação</p>
          <h1>Links de campanha</h1>
          <p>
            Monte o link de cada campanha e veja quantas vendas ele trouxe. A venda conta para a
            última campanha que a pessoa usou nos 7 dias antes de comprar, quando ela aceitou os
            cookies da loja.
          </p>
        </div>
      </header>
      <DashboardCampaignLinks/>
    </>
  );
}
