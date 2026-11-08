import type { ReactNode } from "react";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { DemoCheckoutWorkspace } from "../../components/demo-checkout-workspace";
import { StripeCheckoutCard } from "../../components/stripe-checkout-card";
import { readBuyerSession } from "@/lib/buyer-identity.server";
import { readCheckoutPageOffer } from "@/lib/checkout-page-offer.server";
import { sessionCookieName } from "@/lib/cookies";
import { demoProfileForSubject } from "@/lib/demo-config";
import { oauthService } from "@/lib/oauth-service.server";

const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export default async function DemoCheckoutPage({
  searchParams
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  // Real Stripe checkout path (spike #143) lives alongside the demo one and is
  // reached explicitly via ?real=1&seller=<slug>&product=<slug>. The demo flow
  // below is the untouched default.
  const seller = firstValue(params.seller);
  const product = firstValue(params.product);
  if (firstValue(params.real) === "1" && SLUG.test(seller) && SLUG.test(product)) {
    // Login é obrigatório para COBRAR, não é preferência de UX: sem um subject
    // autenticado o worker vincula o acesso a registration:<e-mail>, identidade
    // que nenhuma conta real alcança (ver lib/buyer-identity.server.ts). A rota
    // create-session já responde 401, mas descobrir isso só depois de clicar em
    // pagar é experiência ruim — e este grupo se chama (protected) sem que o
    // layout seja um gate de auth, então a checagem precisa ser explícita aqui.
    // TODO: /auth/login ainda não honra um parâmetro de retorno, então o
    // comprador perde o deep link. Suportar isso exige tratar open-redirect no
    // callback e está fora do escopo desta fatia.
    // A mesma leitura da sessão que a rota de cobrança faz (#154, achado 2):
    // sessão vencida, subject de registration: e e-mail não verificado vão ao
    // login; session store fora do ar não é "deslogado" e ganha mensagem própria.
    const buyer = await readBuyerSession((await cookies()).get(sessionCookieName())?.value);
    if (buyer.status === "anonymous" || buyer.status === "unverified") redirect("/auth/login");

    const header = <header className="page-header">
      <div><p className="eyebrow">Compra real</p><h1>Finalize seu acesso</h1><p>Pagamento único via Stripe.</p></div>
    </header>;
    if (buyer.status === "unavailable") {
      return <>
        {header}
        <CheckoutNotice title="Não foi possível confirmar seu login">
          Não conseguimos confirmar seu login agora. Tente de novo em alguns
          minutos. Nenhuma cobrança foi realizada.
        </CheckoutNotice>
      </>;
    }

    // Produto e preço vêm do servidor, da mesma projeção que a rota de cobrança
    // usa — nunca do cliente.
    const offer = await readCheckoutPageOffer(seller, product);
    return <>
      {header}
      {offer.kind === "ready"
        ? <StripeCheckoutCard
            sellerSlug={seller}
            productSlug={product}
            sellerName={offer.sellerName}
            productTitle={offer.productTitle}
            priceLabel={offer.priceLabel}
            accountEmail={buyer.email}
          />
        : offer.kind === "unavailable"
          ? <CheckoutNotice title="Loja fora do ar">
              Não conseguimos carregar este produto agora. Tente de novo em
              alguns minutos. Nenhuma cobrança foi realizada.
            </CheckoutNotice>
          : <CheckoutNotice title="Compra indisponível">
              A compra deste produto está indisponível no momento. Nenhuma
              cobrança foi realizada.
            </CheckoutNotice>}
    </>;
  }

  const sessionId = (await cookies()).get(sessionCookieName())?.value;
  const profile = demoProfileForSubject((await oauthService().getSession(sessionId))?.subject);
  if (profile?.persona !== "buyer") redirect(profile?.home ?? "/dashboard");
  return <>
    <header className="page-header">
      <div><p className="eyebrow">Compra demonstrativa</p><h1>Finalize seu acesso</h1><p>Valide o cupom de 100% sem cobrança financeira.</p></div>
    </header>
    <DemoCheckoutWorkspace/>
  </>;
}

function CheckoutNotice({ title, children }: { title: string; children: ReactNode }) {
  return <section className="checkout-card checkout-unavailable" role="alert">
    <h2>{title}</h2>
    <p className="field-help">{children}</p>
  </section>;
}

function firstValue(value: string | string[] | undefined): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value) && typeof value[0] === "string") return value[0];
  return "";
}
