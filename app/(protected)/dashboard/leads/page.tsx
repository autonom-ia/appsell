import { DashboardLeads } from "../../../components/dashboard-leads";

export const dynamic = "force-dynamic";

// Base de leads da loja (#619): quem comprou ou recebeu acesso, com a
// situação do e-mail de marketing, e o CSV de quem pode receber. Em piloto
// por loja; fora dele a página diz que não está disponível.
export default function LeadsPage() {
  return (
    <>
      <header className="page-header">
        <div>
          <p className="eyebrow">Relatórios</p>
          <h1>Leads</h1>
          <p>Quem comprou ou recebeu acesso aos seus produtos, e quem aceitou receber e-mail da loja.</p>
        </div>
      </header>
      <DashboardLeads/>
    </>
  );
}
