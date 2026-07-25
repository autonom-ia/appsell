import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { DemoCheckoutWorkspace } from "../../components/demo-checkout-workspace";
import { StripeCheckoutCard } from "../../components/stripe-checkout-card";
import { resolveCheckoutProduct } from "@/lib/checkout-config";
import { sessionCookieName } from "@/lib/cookies";
import { demoProfileForSubject } from "@/lib/demo-config";
import { readPublicStorefront } from "@/lib/internal-api-client.server";
import { oauthService } from "@/lib/oauth-service.server";
import { parsePublicStorefront } from "@/lib/public-storefront";

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
    const realSession = await realCheckoutSession();
    // Sem sessão OU sem e-mail verificado: a rota de cobrança recusaria (401 /
    // 409), então resolvemos aqui em vez de deixar o comprador descobrir depois
    // de clicar em pagar.
    if (!realSession) redirect("/auth/login");

    // Produto e preço vêm do servidor, da mesma projeção que a rota de cobrança
    // usa — nunca do cliente. Assim o valor mostrado é o valor cobrado.
    const offer = await realCheckoutOffer(seller, product);
    return <>
      <header className="page-header">
        <div><p className="eyebrow">Compra real</p><h1>Finalize seu acesso</h1><p>Pagamento único via Stripe.</p></div>
      </header>
      {offer
        ? <StripeCheckoutCard
            sellerSlug={seller}
            productSlug={product}
            sellerName={offer.sellerName}
            productTitle={offer.productTitle}
            priceLabel={offer.priceLabel}
            accountEmail={realSession.email}
          />
        : <section className="checkout-card checkout-unavailable" role="alert">
            <h2>Compra indisponível</h2>
            <p className="field-help">
              Este produto não está com preço publicado para venda agora. Nenhuma
              cobrança foi realizada.
            </p>
          </section>}
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

// A conta que será cobrada. Exige e-mail VERIFICADO porque é exatamente o que
// resolveBuyerIdentity exige para vincular a compra ao login real; qualquer coisa
// menos que isso seria recusada na rota.
async function realCheckoutSession(): Promise<{ email: string } | null> {
  const sessionId = (await cookies()).get(sessionCookieName())?.value;
  if (!sessionId) return null;
  const session = await oauthService().getSession(sessionId);
  const subject = session?.subject;
  const identity = session?.identity;
  if (typeof subject !== "string" || subject.length === 0) return null;
  if (identity?.emailVerified !== true || typeof identity.email !== "string") return null;
  return { email: identity.email.trim().toLowerCase() };
}

// Mesma fonte de preço da rota de cobrança (projeção pública do storefront), para
// que o valor exibido e o valor cobrado não possam divergir.
async function realCheckoutOffer(
  sellerSlug: string,
  productSlug: string
): Promise<{ sellerName: string; productTitle: string; priceLabel: string } | null> {
  if (!resolveCheckoutProduct(sellerSlug, productSlug)) return null;
  let parsed: ReturnType<typeof parsePublicStorefront>;
  try {
    parsed = parsePublicStorefront(await readPublicStorefront(sellerSlug));
  } catch {
    return null;
  }
  if (!parsed) return null;
  const found = parsed.products.find((candidate) => candidate.slug === productSlug);
  const price = found?.price;
  if (!found || !price) return null;
  if (price.billing_period !== "one_time" || price.currency !== "BRL") return null;
  if (!Number.isInteger(price.unit_amount_minor) || price.unit_amount_minor <= 0) return null;
  return {
    sellerName: parsed.seller.name,
    productTitle: found.title,
    priceLabel: new Intl.NumberFormat("pt-BR", { style: "currency", currency: price.currency })
      .format(price.unit_amount_minor / 100)
  };
}

function firstValue(value: string | string[] | undefined): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value) && typeof value[0] === "string") return value[0];
  return "";
}
