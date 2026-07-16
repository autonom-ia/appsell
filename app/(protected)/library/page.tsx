import { cookies } from "next/headers";

import { LibraryWorkspace } from "../../components/library-workspace";
import { sessionCookieName } from "@/lib/cookies";
import { demoProfileForSubject } from "@/lib/demo-config";
import { oauthService } from "@/lib/oauth-service.server";

export default async function LibraryPage() {
  const sessionId = (await cookies()).get(sessionCookieName())?.value;
  const session = await oauthService().getSession(sessionId);
  const demoCheckoutEnabled = demoProfileForSubject(session?.subject)?.persona === "buyer";
  return (
    <>
      <header className="page-header"><div><p className="eyebrow">Área do aluno</p><h1>Minha biblioteca</h1><p>Somente produtos publicados com acesso ativo para esta conta.</p></div></header>
      <LibraryWorkspace demoCheckoutEnabled={demoCheckoutEnabled}/>
    </>
  );
}
