import { PayoutConnectCard } from "../../../components/payout-connect-card";
import { SettingsNav } from "../../../components/settings-nav";

// Recebimentos moraram em /payouts, no menu principal. Agora são uma área de
// Configurações (#352). O endereço antigo redireciona (next.config.ts).
export default function PayoutSettingsPage() {
  return (
    <>
      <header className="page-header">
        <div>
          <p className="eyebrow">Configurações</p>
          <h1>Recebimentos</h1>
          <p>
            Conecte a conta que recebe o valor das suas vendas e acompanhe a verificação
            sem sair da Autonom.ia.
          </p>
        </div>
      </header>
      <SettingsNav active="payouts"/>
      <PayoutConnectCard />
    </>
  );
}
