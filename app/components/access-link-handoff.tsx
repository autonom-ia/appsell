"use client";

import { useEffect } from "react";

import { ACCESS_LINK_HANDOFF_KEY, ACCESS_LINK_CONTINUE_PATH } from "./access-link-handoff-shared";

/**
 * Carries the invitation across the login round trip without putting it on the
 * network again.
 *
 * The address of an invitation is the credential. Sending the visitor to
 * `/auth/login?next=/c/<token>` would write that credential into the post-login
 * cookie, which is then replayed on every same-origin request until the callback
 * clears it. The token is parked in this tab's session storage instead, and the
 * redirect carries a path that contains no secret at all.
 */
export function AccessLinkHandoff({ token }: { token: string }) {
  useEffect(() => {
    try {
      window.sessionStorage.setItem(ACCESS_LINK_HANDOFF_KEY, token);
    } catch {
      // Storage can be blocked. The continue page then explains that the link
      // has to be opened again, which is better than leaking it into a cookie.
    }
    window.location.replace(
      `/auth/login?next=${encodeURIComponent(ACCESS_LINK_CONTINUE_PATH)}`
    );
  }, [token]);

  return (
    <section className="access-redeem" aria-labelledby="access-handoff-title">
      <p className="eyebrow">Convite</p>
      <h1 id="access-handoff-title">Entre para usar o convite</h1>
      <p role="status" aria-live="polite">
        Levando você para o acesso. Depois de entrar, o convite continua daqui.
      </p>
      <noscript>
        <p>
          Ative o JavaScript para usar este convite, ou entre primeiro e abra o link outra vez.
        </p>
      </noscript>
    </section>
  );
}
