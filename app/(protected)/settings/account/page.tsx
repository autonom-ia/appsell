import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { AccountWorkspace } from "../../../components/account-workspace";
import { sessionCookieName } from "@/lib/cookies";
import {
  EXPORT_ACCOUNT_DATA_CSRF_ACTION,
  REQUEST_ACCOUNT_DELETION_CSRF_ACTION,
  csrfTokenForSession
} from "@/lib/mutation-protection.server";
import { identityOAuthClient, oauthRuntimeMode, oauthService } from "@/lib/oauth-service.server";

export const dynamic = "force-dynamic";

// Minha conta (#351): a menor superfície do trilho e o usuário que mais
// cresce. Os tokens das duas mutações nascem aqui, no servidor, presos à
// sessão — o mesmo desenho do resgate de link e do acesso gratuito.
export default async function AccountPage() {
  const sessionId = (await cookies()).get(sessionCookieName())?.value;
  const session = await oauthService().getSession(sessionId);
  if (!sessionId || !session) redirect("/auth/login?next=%2Fsettings%2Faccount");
  return (
    <>
      <header className="page-header">
        <div>
          <p className="eyebrow">Minha conta</p>
          <h1>Seus dados e seus direitos</h1>
          <p>
            O que sabemos sobre você, o que você tem acesso, e como levar ou
            apagar tudo isso.
          </p>
        </div>
      </header>
      <AccountWorkspace
        csrfExport={csrfTokenForSession(sessionId, EXPORT_ACCOUNT_DATA_CSRF_ACTION)}
        csrfDeletion={csrfTokenForSession(sessionId, REQUEST_ACCOUNT_DELETION_CSRF_ACTION)}
        passwordRecoveryHref={passwordRecoveryHref()}
      />
    </>
  );
}

// Só existe com Identity real. No runtime local não há para onde apontar, e
// a tela diz isso em vez de mostrar um link morto.
function passwordRecoveryHref(): string | null {
  if (oauthRuntimeMode() !== "identity") return null;
  try {
    return identityOAuthClient().passwordRecoveryUrl().toString();
  } catch {
    return null;
  }
}
