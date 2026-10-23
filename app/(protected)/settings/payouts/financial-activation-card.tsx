"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  FINANCIAL_ACTIVATION_REASON_COPY,
  type FinancialActivationReason,
  type FinancialActivationStatus
} from "@/lib/financial-activation";

// "Ativar vendas" (#382). Só aparece para loja dentro da flag de ativação: fora
// dela (a Hub2You, hoje) a página de Recebimentos fica exatamente como era.

interface Seller {
  id: string;
  role: string;
}

interface ActivationResponse {
  activation: {
    self_service: boolean;
    status?: FinancialActivationStatus;
    reason?: FinancialActivationReason | null;
  };
  csrf: { activate: string } | null;
}

type CardState =
  | { kind: "hidden" }
  | { kind: "active" }
  | { kind: "available" }
  | { kind: "activating" }
  | { kind: "blocked"; reason: FinancialActivationReason }
  | { kind: "error" };

const TIMEOUT_MS = 8_000;

export function FinancialActivationCard() {
  const [state, setState] = useState<CardState>({ kind: "hidden" });
  const [seller, setSeller] = useState<Seller | null>(null);
  const csrfToken = useRef("");
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const focusHeadingNext = useRef(false);

  useEffect(() => {
    if (focusHeadingNext.current && headingRef.current) {
      focusHeadingNext.current = false;
      headingRef.current.focus();
    }
  }, [state]);

  const loadStatus = useCallback(async (owned: Seller, signal?: AbortSignal) => {
    const response = await fetch(`/api/financial/seller-activation/${owned.id}`, {
      cache: "no-store",
      signal
    });
    if (!response.ok) return setState({ kind: "hidden" });
    const payload = (await response.json()) as ActivationResponse;
    const { activation } = payload;
    if (!activation.self_service) return setState({ kind: "hidden" });
    csrfToken.current = payload.csrf?.activate ?? "";
    if (activation.status === "active") return setState({ kind: "active" });
    if (activation.status === "available") return setState({ kind: "available" });
    if (activation.status === "blocked" && activation.reason) {
      return setState({ kind: "blocked", reason: activation.reason });
    }
    setState({ kind: "hidden" });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);
    void fetch("/api/sellers", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) return;
        const payload = (await response.json()) as { sellers?: Seller[] };
        const owned = (payload.sellers ?? []).find(
          (candidate) => candidate.role === "seller_owner" || candidate.role === "owner"
        );
        if (!owned) return;
        setSeller(owned);
        await loadStatus(owned, controller.signal);
      })
      // Falhar a leitura deixa o card escondido: a página segue como antes.
      .catch(() => undefined)
      .finally(() => clearTimeout(timeout));
    return () => {
      controller.abort();
      clearTimeout(timeout);
    };
  }, [loadStatus]);

  const activate = useCallback(async () => {
    if (!seller) return;
    focusHeadingNext.current = true;
    setState({ kind: "activating" });
    try {
      const body = new FormData();
      body.set("_csrf", csrfToken.current);
      const response = await fetch(`/api/financial/seller-activation/${seller.id}`, {
        method: "POST",
        body
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { error?: string } | null;
        if (payload?.error === "payout_account_not_ready") {
          return setState({ kind: "blocked", reason: "payout_account_not_ready" });
        }
        return setState({ kind: "error" });
      }
      await loadStatus(seller);
    } catch {
      setState({ kind: "error" });
    }
  }, [loadStatus, seller]);

  if (state.kind === "hidden") return null;

  return (
    <section className="payout-card" aria-live="polite">
      <p className="eyebrow">Vendas</p>
      {state.kind === "active" ? (
        <>
          <h2 ref={headingRef} tabIndex={-1}>Vendas ativas</h2>
          <p>Sua loja já pode vender os produtos publicados com preço.</p>
        </>
      ) : null}
      {state.kind === "available" || state.kind === "activating" ? (
        <>
          <h2 ref={headingRef} tabIndex={-1}>Ative as vendas da sua loja</h2>
          <p>
            Com as vendas ativas, os produtos publicados com preço passam a aceitar
            pagamento e o comprador recebe o acesso logo depois.
          </p>
          <button
            type="button"
            className="primary-button"
            disabled={state.kind === "activating"}
            aria-busy={state.kind === "activating"}
            onClick={() => void activate()}
          >
            {state.kind === "activating" ? "Ativando…" : "Ativar vendas"}
          </button>
        </>
      ) : null}
      {state.kind === "blocked" ? (
        <>
          <h2 ref={headingRef} tabIndex={-1}>Vendas ainda não ativadas</h2>
          <p className="form-note">{FINANCIAL_ACTIVATION_REASON_COPY[state.reason]}</p>
        </>
      ) : null}
      {state.kind === "error" ? (
        <>
          <h2 ref={headingRef} tabIndex={-1}>Não foi possível ativar agora</h2>
          <p className="form-error">
            Nada foi alterado na sua loja. Tente de novo em alguns minutos.
          </p>
          <button type="button" className="secondary-button" onClick={() => void activate()}>
            Tentar de novo
          </button>
        </>
      ) : null}
    </section>
  );
}
