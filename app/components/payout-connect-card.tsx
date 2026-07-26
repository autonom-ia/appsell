"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import type { StripeConnectInstance } from "@stripe/connect-js";
import {
  ConnectAccountOnboarding,
  ConnectComponentsProvider
} from "@stripe/react-connect-js";

import {
  PAYOUT_STAGE_COPY,
  payoutStageFrom,
  type PayoutAccountStatus,
  type PayoutStage
} from "@/lib/payout-status";

// "Conectar conta Autonom.ia" — a verificação de identidade acontece AQUI
// dentro (componente embarcado), nunca numa tela externa. A copy fala a língua
// do produto: conta de recebimentos, verificação, análise. O nome do provedor
// só aparece onde a lei exige — dentro do próprio formulário embarcado, no
// aceite do contrato de serviço.

interface Seller {
  id: string;
  slug: string;
  display_name: string;
  role: string;
}

interface PayoutAccountResponse {
  payout_account: (PayoutAccountStatus & { payout_account_id: string }) | null;
  csrf?: { connect?: string };
}

type CardState =
  | "loading"
  | "ready"
  | "connecting"
  | "onboarding"
  | "no_seller"
  | "unauthorized"
  | "unavailable"
  | "unconfigured"
  | "error";

const STAGES: Array<{ key: "created" | "verified" | "receiving"; label: string }> = [
  { key: "created", label: "Conta criada" },
  { key: "verified", label: "Verificação" },
  { key: "receiving", label: "Pronta para receber" }
];

function milestoneProgress(stage: PayoutStage): number {
  if (stage === "active") return 3;
  if (stage === "under_review" || stage === "action_required") return 2;
  if (stage === "verification_pending") return 1;
  return 0;
}

export function PayoutConnectCard() {
  const [state, setState] = useState<CardState>("loading");
  const [seller, setSeller] = useState<Seller | null>(null);
  const [account, setAccount] = useState<PayoutAccountStatus | null>(null);
  const [connectInstance, setConnectInstance] = useState<StripeConnectInstance | null>(null);
  const csrfToken = useRef<string>("");
  // Last-request-wins guard: loadStatus is reachable from mount, retry and the
  // embedded-flow exit. Without it, whichever response resolves LAST would
  // overwrite the state — possibly showing the producer a stale stage about
  // their own money. Unmount bumps the sequence, so late responses are dropped
  // instead of setting state on an unmounted component.
  const loadSequence = useRef(0);
  // Focus lands on the new section's heading after a USER-initiated
  // transition (connect, exit, retry): the clicked control disappears from the
  // DOM, and without this the keyboard/screen-reader user is dropped on
  // <body> with no announcement of where the content went. Page-load
  // transitions do not steal focus — only explicit actions arm the flag.
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const focusHeadingNext = useRef(false);

  useEffect(() => {
    if (focusHeadingNext.current && headingRef.current) {
      focusHeadingNext.current = false;
      headingRef.current.focus();
    }
  }, [state]);

  const loadStatus = useCallback(async (activeSeller: Seller) => {
    const sequence = ++loadSequence.current;
    const fresh = () => sequence === loadSequence.current;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8_000);
    try {
      const response = await fetch(`/api/sellers/${activeSeller.id}/payout-account`, {
        cache: "no-store",
        signal: controller.signal
      });
      if (!fresh()) return;
      if (response.status === 401) return setState("unauthorized");
      if (response.status === 503) return setState("unavailable");
      if (!response.ok) return setState("error");
      const payload = (await response.json()) as PayoutAccountResponse;
      if (!fresh()) return;
      csrfToken.current = payload.csrf?.connect ?? "";
      setAccount(payload.payout_account);
      setState("ready");
    } catch {
      if (fresh()) setState("error");
    } finally {
      clearTimeout(timeout);
    }
  }, []);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8_000);
    void fetch("/api/sellers", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!active) return;
        if (response.status === 401) return setState("unauthorized");
        if (response.status === 503) return setState("unavailable");
        if (!response.ok) return setState("error");
        const payload = (await response.json()) as { sellers?: Seller[] };
        const owned = (payload.sellers ?? []).find(
          (candidate) => candidate.role === "seller_owner" || candidate.role === "owner"
        );
        if (!owned) return setState("no_seller");
        setSeller(owned);
        await loadStatus(owned);
      })
      .catch(() => {
        if (active) setState("error");
      })
      .finally(() => clearTimeout(timeout));
    return () => {
      active = false;
      loadSequence.current += 1;
      controller.abort();
      clearTimeout(timeout);
    };
  }, [loadStatus]);

  const fetchClientSecret = useCallback(async () => {
    // The embedded component re-invokes this whenever its session needs
    // renewing — long after startOnboarding's try/catch returned. A failure
    // here (expired session, rotated CSRF, network) must surface in OUR UI, in
    // our words, not only inside the provider's frame: fall the card back to
    // the error state, which offers the retry path, and still throw so the
    // embedded component stops cleanly.
    try {
      if (!seller) throw new Error("seller_unavailable");
      const body = new FormData();
      body.set("_csrf", csrfToken.current);
      const response = await fetch(`/api/sellers/${seller.id}/payout-account`, {
        method: "POST",
        body
      });
      if (!response.ok) throw new Error("payout_session_unavailable");
      const payload = (await response.json()) as { client_secret?: string };
      if (!payload.client_secret) throw new Error("payout_session_unavailable");
      return payload.client_secret;
    } catch (error) {
      focusHeadingNext.current = true;
      setConnectInstance(null);
      setState("error");
      throw error;
    }
  }, [seller]);

  const startOnboarding = useCallback(async () => {
    focusHeadingNext.current = true;
    const publishableKey = process.env.NEXT_PUBLIC_APPSELL_STRIPE_PUBLISHABLE_KEY ?? "";
    if (!publishableKey) return setState("unconfigured");
    setState("connecting");
    try {
      const { loadConnectAndInitialize } = await import("@stripe/connect-js");
      const instance = loadConnectAndInitialize({
        publishableKey,
        fetchClientSecret,
        locale: "pt-BR",
        appearance: {
          variables: {
            colorPrimary: "#1e3a8a",
            colorText: "#0f172a",
            borderRadius: "12px",
            fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
          }
        }
      });
      setConnectInstance(instance);
      setState("onboarding");
    } catch {
      setState("error");
    }
  }, [fetchClientSecret]);

  const finishOnboarding = useCallback(() => {
    focusHeadingNext.current = true;
    setConnectInstance(null);
    if (seller) {
      setState("loading");
      void loadStatus(seller);
    }
  }, [seller, loadStatus]);

  if (state === "loading") {
    return (
      <section className="payout-card" aria-busy="true">
        <p className="eyebrow">Recebimentos</p>
        <div className="payout-skeleton" aria-hidden="true" />
        <p className="form-note">Verificando o status da sua conta…</p>
      </section>
    );
  }

  if (state === "unauthorized") {
    return (
      <section className="payout-card">
        <p className="eyebrow">Recebimentos</p>
        <h2 ref={headingRef} tabIndex={-1}>Sessão expirada</h2>
        <p className="form-error">
          Sua sessão expirou. <a href="/auth/login">Entre novamente</a> para continuar.
        </p>
      </section>
    );
  }

  if (state === "no_seller") {
    return (
      <section className="payout-card">
        <p className="eyebrow">Recebimentos</p>
        <h2 ref={headingRef} tabIndex={-1}>Crie seu espaço primeiro</h2>
        <p>
          Para conectar uma conta de recebimentos, você precisa de um espaço de vendas
          ativo como proprietário.
        </p>
        <Link className="secondary-button" href="/products">
          Criar meu espaço de vendas
        </Link>
      </section>
    );
  }

  if (state === "unavailable" || state === "unconfigured" || state === "error") {
    return (
      <section className="payout-card">
        <p className="eyebrow">Recebimentos</p>
        <h2 ref={headingRef} tabIndex={-1}>Não foi possível carregar agora</h2>
        <p className="form-error">
          {state === "unconfigured"
            ? "A conexão de recebimentos ainda não está habilitada neste ambiente."
            : "O serviço de recebimentos está indisponível no momento. Nada foi alterado na sua conta."}
        </p>
        {state !== "unconfigured" && seller ? (
          <button
            type="button"
            className="secondary-button"
            onClick={() => {
              focusHeadingNext.current = true;
              setState("loading");
              void loadStatus(seller);
            }}
          >
            Tentar de novo
          </button>
        ) : null}
      </section>
    );
  }

  if (state === "onboarding" && connectInstance) {
    return (
      <section className="payout-card payout-onboarding">
        <p className="eyebrow">Recebimentos</p>
        <h2 ref={headingRef} tabIndex={-1}>Verificação de identidade</h2>
        <p>
          Preencha os dados abaixo sem sair da Autonom.ia. Você pode parar e continuar
          depois — nada se perde.
        </p>
        <div className="payout-embedded">
          <ConnectComponentsProvider connectInstance={connectInstance}>
            <ConnectAccountOnboarding onExit={finishOnboarding} />
          </ConnectComponentsProvider>
        </div>
        <button type="button" className="secondary-button" onClick={finishOnboarding}>
          Concluir depois
        </button>
      </section>
    );
  }

  const stage = payoutStageFrom(account);
  const copy = PAYOUT_STAGE_COPY[stage];
  const progress = milestoneProgress(stage);
  const busy = state === "connecting";

  return (
    <section className="payout-card" aria-busy={busy}>
      <p className="eyebrow">Recebimentos</p>
      <span className={`payout-badge payout-badge-${stage}`}>{copy.badge}</span>
      <h2 ref={headingRef} tabIndex={-1}>{copy.title}</h2>
      <p>{copy.description}</p>
      <ol className="payout-rail" aria-label="Etapas da conta de recebimentos">
        {STAGES.map((milestone, index) => {
          const reached = index < progress;
          const current = index === progress && stage !== "active";
          return (
            <li
              key={milestone.key}
              className={
                reached ? "payout-step done" : current ? "payout-step current" : "payout-step"
              }
              aria-current={current ? "step" : undefined}
            >
              <span className="payout-step-dot" aria-hidden="true" />
              {milestone.label}
            </li>
          );
        })}
      </ol>
      {copy.action ? (
        <button
          type="button"
          className="primary-button"
          onClick={() => void startOnboarding()}
          disabled={busy}
        >
          {busy ? "Preparando…" : copy.action}
        </button>
      ) : null}
      <p className="form-note">
        Seus dados são usados apenas para a verificação de identidade exigida por lei.
        A Autonom.ia não guarda seus documentos.
      </p>
    </section>
  );
}
