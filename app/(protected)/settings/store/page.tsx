import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { SettingsNav } from "../../../components/settings-nav";
import { StoreSettingsWorkspace } from "../../../components/store-settings-workspace";
import { sessionCookieName } from "@/lib/cookies";
import { oauthService } from "@/lib/oauth-service.server";
import { SITE } from "@/lib/site";

export const dynamic = "force-dynamic";

// Configurações da loja (#352): nome, texto próprio, cor e logo. Os tokens de
// cada mutação chegam pelo GET da rota, presos à sessão, como na capa.
export default async function StoreSettingsPage() {
  const sessionId = (await cookies()).get(sessionCookieName())?.value;
  const session = await oauthService().getSession(sessionId);
  if (!sessionId || !session) redirect("/auth/login?next=%2Fsettings%2Fstore");
  return (
    <>
      <header className="page-header">
        <div>
          <p className="eyebrow">Configurações</p>
          <h1>Sua loja</h1>
          <p>Como sua loja se apresenta para quem chega pelo seu link.</p>
        </div>
      </header>
      <SettingsNav active="store"/>
      <StoreSettingsWorkspace publicOrigin={SITE.origem}/>
    </>
  );
}
