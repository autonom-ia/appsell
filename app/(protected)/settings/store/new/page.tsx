import { SellerOnboardingCard } from "../../../../components/seller-onboarding-card";

// A criação da loja precisa de endereço próprio. Ela vivia dentro da Visão
// geral e de Produtos, e essas duas telas deixaram de aparecer para quem ainda
// não tem loja — sem esta rota, o cadastro do "Vender agora" (#341) terminaria
// num beco sem saída.
export default function NewStorePage() {
  return (
    <>
      <header className="page-header">
        <div>
          <p className="eyebrow">Comece por aqui</p>
          <h1>Criar minha loja</h1>
          <p>
            A loja é o espaço onde seus produtos ficam e onde seus alunos compram.
            Você pode mudar o nome depois; o endereço público fica fixo.
          </p>
        </div>
      </header>
      <SellerOnboardingCard/>
    </>
  );
}
