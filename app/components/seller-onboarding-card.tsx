"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { autoSelectableSpace, SellerSpace, sellerRoleLabel } from "./seller-space";

type Seller = SellerSpace;

interface SellerOnboardingCardProps {
  /**
   * Hides the active-spaces summary when the account has a single space that
   * the catalog workspace already labels on the same page. Surfaces without
   * that label, such as the dashboard, keep the summary visible.
   */
  hideSingleSpaceSummary?: boolean;
}

export function SellerOnboardingCard({ hideSingleSpaceSummary = false }: SellerOnboardingCardProps = {}) {
  const [sellers, setSellers] = useState<Seller[] | null>(null);
  const [status, setStatus] = useState<"loading" | "idle" | "saving" | "preview" | "unauthorized" | "conflict" | "invalid_slug" | "invalid_name" | "error">("loading");
  const [creationSucceeded, setCreationSucceeded] = useState(false);
  const idempotencyKey = useRef<string | null>(null);

  const loadSellers = useCallback(async (): Promise<boolean> => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5_000);
    try {
      const response = await fetch("/api/sellers", {
        cache: "no-store",
        signal: controller.signal
      });
      if (response.status === 401) { setStatus("unauthorized"); return false; }
      if (response.status === 503) { setStatus("preview"); return false; }
      if (!response.ok) { setStatus("error"); return false; }
      const payload = await response.json() as { sellers?: Seller[] };
      setSellers(Array.isArray(payload.sellers) ? payload.sellers : []);
      setStatus("idle");
      return true;
    } catch {
      return false;
    } finally {
      clearTimeout(timeout);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const timeout = setTimeout(() => controller.abort(), 5_000);
    void fetch("/api/sellers", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (response.status === 401) return setStatus("unauthorized");
        if (response.status === 503) return setStatus("preview");
        if (!response.ok) return setStatus("error");
        const payload = await response.json() as { sellers?: Seller[] };
        setSellers(Array.isArray(payload.sellers) ? payload.sellers : []);
        setStatus("idle");
      })
      .catch(() => {
        if (active) setStatus("error");
      })
      .finally(() => clearTimeout(timeout));
    return () => {
      active = false;
      clearTimeout(timeout);
      controller.abort();
    };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const csrfCookie = process.env.NODE_ENV === "production"
      ? "__Host-appsell_seller_csrf"
      : "appsell_seller_csrf";
    data.set("_csrf", readCookie(csrfCookie));
    idempotencyKey.current ??= crypto.randomUUID();
    data.set("idempotency_key", idempotencyKey.current);
    setStatus("saving");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8_000);
    try {
      const response = await fetch("/api/sellers", {
        method: "POST",
        body: data,
        signal: controller.signal
      });
      if (!response.ok) {
        if (response.status === 401) setStatus("unauthorized");
        else if (response.status === 409) setStatus("conflict");
        else if (response.status === 422) {
          const payload = await response.json().catch(() => null) as { field?: string } | null;
          const field = payload?.field === "display_name" ? "display_name" : "slug";
          setStatus(field === "display_name" ? "invalid_name" : "invalid_slug");
          const input = form.elements.namedItem(field);
          if (input instanceof HTMLInputElement) input.focus();
        } else if (response.status === 503) setStatus("preview");
        else setStatus("error");
        return;
      }
      idempotencyKey.current = null;
      form.reset();
      setCreationSucceeded(true);
      await loadSellers();
      window.dispatchEvent(new Event("appsell:sellers-changed"));
    } catch {
      setStatus("error");
    } finally {
      clearTimeout(timeout);
    }
  }

  if (sellers?.length) {
    if (hideSingleSpaceSummary && autoSelectableSpace(sellers)) return null;
    return <section className="seller-onboarding seller-ready"><div><p className="eyebrow">Espaços ativos</p><h2>{sellers.length === 1 ? "Seu espaço está pronto" : `${sellers.length} espaços disponíveis`}</h2><p>Escolha abaixo qual espaço deseja administrar.</p></div><ul>{sellers.map((seller) => <li key={seller.id}><strong>{seller.display_name}</strong><span>@{seller.slug} · {sellerRoleLabel(seller.role)}</span></li>)}</ul></section>;
  }

  return (
    <section className="seller-onboarding">
      <div><p className="eyebrow">Ativação do produtor</p><h2>Crie sua loja</h2><p>Use a loja para organizar seus produtos, cursos e alunos.</p></div>
      <form action="/api/sellers" method="post" onSubmit={submit}>
        <input type="hidden" name="_csrf" defaultValue=""/>
        <input type="hidden" name="idempotency_key" defaultValue=""/>
        <label>Nome do negócio<input name="display_name" minLength={2} maxLength={120} required autoComplete="organization" placeholder="Ex.: Studio Rodrigo…" aria-invalid={status === "invalid_name"} aria-describedby={status === "invalid_name" ? "seller-name-error" : undefined}/>{status === "invalid_name" ? <span id="seller-name-error" className="form-error">Informe o nome sem espaços extras no início ou no fim.</span> : null}</label>
        <label>Endereço público<input name="slug" minLength={3} maxLength={63} required autoComplete="off" autoCapitalize="none" spellCheck={false} pattern="[a-z0-9]+(?:-[a-z0-9]+)*" placeholder="studio-rodrigo…" aria-invalid={status === "invalid_slug"} aria-describedby={status === "invalid_slug" ? "seller-slug-error" : undefined}/>{status === "invalid_slug" ? <span id="seller-slug-error" className="form-error">Use letras minúsculas, números e hífens.</span> : null}</label>
        <button className="primary-button" type="submit" disabled={status === "saving" || status === "loading"}>{status === "saving" ? "Criando…" : status === "loading" ? "Verificando…" : "Ativar meu espaço"}</button>
        <div aria-live="polite" aria-atomic="true">
          {status === "preview" ? <p className="form-note">A API está indisponível. Você pode tentar novamente sem duplicar o cadastro.</p> : null}
          {creationSucceeded && status !== "idle" ? <p className="form-note">Espaço criado. Não repita o cadastro; atualize a lista abaixo.</p> : null}
          {status === "unauthorized" ? <p className="form-error">Sua sessão expirou. <a href="/auth/login">Entre novamente</a>.</p> : null}
          {status === "conflict" ? <p className="form-error">Este endereço já está em uso ou sua conta já possui um espaço.</p> : null}
          {status === "error" ? <p className="form-error">Não foi possível concluir. Tente novamente; a mesma tentativa não será duplicada.</p> : null}
          {creationSucceeded && status !== "idle" ? <button type="button" className="secondary-button" onClick={() => void loadSellers()}>Atualizar lista de espaços</button> : null}
        </div>
        <noscript><p className="form-note">Ative JavaScript para assinar esta operação com segurança.</p></noscript>
      </form>
    </section>
  );
}

function readCookie(name: string) {
  const prefix = `${name}=`;
  const value = document.cookie.split("; ").find((part) => part.startsWith(prefix));
  return value ? decodeURIComponent(value.slice(prefix.length)) : "";
}
