import { PayoutConnectCard } from "../../components/payout-connect-card";

export default function PayoutsPage() {
  return (
    <>
      <header className="page-header">
        <div>
          <p className="eyebrow">Recebimentos</p>
          <h1>Sua conta de recebimentos</h1>
          <p>
            Conecte a conta que recebe o valor das suas vendas e acompanhe a verificação
            sem sair da Autonom.ia.
          </p>
        </div>
      </header>
      <PayoutConnectCard />
    </>
  );
}
