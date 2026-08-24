"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { ACCESS_LINK_CONTINUE_PATH, ACCESS_LINK_HANDOFF_KEY } from "./access-link-handoff-shared";

type RedeemState =
  | "idle"
  | "submitting"
  | "granted"
  | "already"
  | "exhausted"
  | "unavailable"
  | "requires_checkout"
  | "rate_limited"
  | "missing"
  | "error";

/**
 * Claims an invitation seat.
 *
 * `token` is null when the visitor came back from login: the invitation was
 * parked in this tab's session storage precisely so that it never travelled
 * through the post-login cookie. It is read once and removed.
 */
export function AccessLinkRedeemWorkspace(
  { csrf, token }: { csrf: string; token: string | null }
) {
  const [state, setState] = useState<RedeemState>("idle");
  const started = useRef(false);

  const redeem = useCallback(async () => {
    if (state === "submitting" || state === "granted" || state === "already") return;
    const carried = token ?? readHandoffToken();
    if (!carried) return setState("missing");
    setState("submitting");
    const data = new FormData();
    data.set("_csrf", csrf);
    data.set("idempotency_key", crypto.randomUUID());
    data.set("token", carried);
    try {
      const response = await fetch("/api/library/access-links/redeem", {
        method: "POST",
        body: data
      });
      if (response.status === 401) {
        // The path carries no secret. The invitation stays in session storage,
        // so signing in again resumes without putting it back on the network.
        stashHandoffToken(carried);
        window.location.href =
          `/auth/login?next=${encodeURIComponent(ACCESS_LINK_CONTINUE_PATH)}`;
        return;
      }
      if (!response.ok) {
        const payload = await response.json().catch(() => null) as { error?: string } | null;
        if (payload?.error === "access_link_exhausted") return setState("exhausted");
        if (payload?.error === "access_link_unavailable") return setState("unavailable");
        if (payload?.error === "offer_requires_checkout") return setState("requires_checkout");
        if (response.status === 429) return setState("rate_limited");
        return setState("error");
      }
      const payload = await response.json() as { seat_consumed?: boolean };
      clearHandoffToken();
      setState(payload.seat_consumed === false ? "already" : "granted");
    } catch {
      setState("error");
    }
  }, [csrf, state, token]);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void redeem();
  }, [redeem]);

  return (
    <section className="access-redeem" aria-labelledby="access-redeem-title">
      <p className="eyebrow">Convite</p>
      <h1 id="access-redeem-title">{heading(state)}</h1>
      <p role="status" aria-live="polite">{explanation(state)}</p>
      <div className="error-actions">
        {state === "granted" || state === "already" ? (
          <Link className="primary-button" href="/library">Ir para o meu conteúdo</Link>
        ) : null}
        {state === "error" || state === "rate_limited" ? (
          <button type="button" className="primary-button" onClick={() => void redeem()}>
            Tentar novamente
          </button>
        ) : null}
      </div>
    </section>
  );
}

function readHandoffToken() {
  try {
    return window.sessionStorage.getItem(ACCESS_LINK_HANDOFF_KEY);
  } catch {
    return null;
  }
}

function stashHandoffToken(token: string) {
  try {
    window.sessionStorage.setItem(ACCESS_LINK_HANDOFF_KEY, token);
  } catch {
    // Nothing to do: the visitor will be asked to open the link again.
  }
}

function clearHandoffToken() {
  try {
    window.sessionStorage.removeItem(ACCESS_LINK_HANDOFF_KEY);
  } catch {
    // Already gone, or storage is blocked.
  }
}

function heading(state: RedeemState) {
  if (state === "submitting" || state === "idle") return "Liberando seu acesso…";
  if (state === "granted") return "Acesso liberado";
  if (state === "already") return "Você já tinha acesso";
  if (state === "exhausted") return "Este convite está lotado";
  if (state === "unavailable") return "Convite indisponível";
  if (state === "requires_checkout") return "Este produto é pago";
  if (state === "rate_limited") return "Muitas tentativas";
  if (state === "missing") return "Abra o convite outra vez";
  return "Não foi possível liberar";
}

function explanation(state: RedeemState) {
  if (state === "submitting" || state === "idle") return "Um instante enquanto confirmamos sua vaga.";
  if (state === "granted") return "Pronto. O conteúdo já está na sua biblioteca.";
  if (state === "already") {
    return "Reabrir o convite não consome outra vaga. Seu acesso continua o mesmo.";
  }
  if (state === "exhausted") return "Todas as vagas foram usadas. Peça um novo convite a quem enviou.";
  if (state === "unavailable") {
    return "O convite foi revogado, venceu, ou o endereço está incorreto. Peça um novo a quem enviou.";
  }
  if (state === "requires_checkout") return "Ele passou a ser vendido e precisa de pagamento.";
  if (state === "rate_limited") return "Aguarde um minuto antes de tentar de novo.";
  if (state === "missing") {
    return "Perdemos o convite no caminho. Abra o link que você recebeu novamente, agora já conectado.";
  }
  return "Algo falhou no caminho. Tente novamente em instantes.";
}
