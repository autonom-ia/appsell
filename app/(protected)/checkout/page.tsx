import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { DemoCheckoutWorkspace } from "../../components/demo-checkout-workspace";
import { StripeCheckoutButton } from "../../components/stripe-checkout-button";
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
    return <>
      <header className="page-header">
        <div><p className="eyebrow">Compra real</p><h1>Finalize seu acesso</h1><p>Pagamento único via Stripe.</p></div>
      </header>
      <StripeCheckoutButton sellerSlug={seller} productSlug={product}/>
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

function firstValue(value: string | string[] | undefined): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value) && typeof value[0] === "string") return value[0];
  return "";
}
