"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

type RedeemState =
  | "idle"
  | "submitting"
  | "granted"
  | "already"
  | "exhausted"
  | "unavailable"
  | "requires_checkout"
  | "rate_limited"
  | "error";

export function AccessLinkRedeemWorkspace({ csrf, token }: { csrf: string; token: string }) {
  const [state, setState] = useState<RedeemState>("idle");
  const started = useRef(false);

  const redeem = useCallback(async () => {
    if (state === "submitting" || state === "granted" || state === "already") return;
    setState("submitting");
    const data = new FormData();
    data.set("_csrf", csrf);
    data.set("idempotency_key", crypto.randomUUID());
    data.set("token", token);
    try {
      const response = await fetch("/api/library/access-links/redeem", {
        method: "POST",
        body: data
      });
      if (response.status === 401) {
        window.location.href = `/auth/login?next=${encodeURIComponent(window.location.pathname)}`;
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

function heading(state: RedeemState) {
  if (state === "submitting" || state === "idle") return "Liberando seu acesso…";
  if (state === "granted") return "Acesso liberado";
  if (state === "already") return "Você já tinha acesso";
  if (state === "exhausted") return "Este convite está lotado";
  if (state === "unavailable") return "Convite indisponível";
  if (state === "requires_checkout") return "Este produto é pago";
  if (state === "rate_limited") return "Muitas tentativas";
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
  return "Algo falhou no caminho. Tente novamente em instantes.";
}
