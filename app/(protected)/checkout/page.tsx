import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { DemoCheckoutWorkspace } from "../../components/demo-checkout-workspace";
import { sessionCookieName } from "@/lib/cookies";
import { demoProfileForSubject } from "@/lib/demo-config";
import { oauthService } from "@/lib/oauth-service.server";

export default async function DemoCheckoutPage() {
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
