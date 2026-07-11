"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

interface Seller {
  id: string;
  slug: string;
  display_name: string;
  role: string;
}

export function SellerOnboardingCard() {
  const [sellers, setSellers] = useState<Seller[] | null>(null);
  const [status, setStatus] = useState<"loading" | "idle" | "saving" | "preview" | "unauthorized" | "conflict" | "invalid_slug" | "invalid_name" | "error">("loading");
  const idempotencyKey = useRef<string | null>(null);

  const loadSellers = useCallback(async () => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5_000);
    try {
      const response = await fetch("/api/sellers", {
        cache: "no-store",
        signal: controller.signal
      });
      if (response.status === 401) return setStatus("unauthorized");
      if (response.status === 503) return setStatus("preview");
      if (!response.ok) return setStatus("error");
      const payload = await response.json() as { sellers?: Seller[] };
      setSellers(Array.isArray(payload.sellers) ? payload.sellers : []);
      setStatus("idle");
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
      await loadSellers();
    } catch {
      setStatus("error");
    } finally {
      clearTimeout(timeout);
    }
  }

  if (sellers?.length) {
    const seller = sellers[0]!;
    return <section className="seller-onboarding seller-ready"><p className="eyebrow">Tenant ativo</p><h2>{seller.display_name}</h2><p><strong>@{seller.slug}</strong> · acesso {seller.role}</p><span>Persistido no PostgreSQL com RLS</span></section>;
  }

  return (
    <section className="seller-onboarding">
      <div><p className="eyebrow">Ativação do produtor</p><h2>Crie seu espaço de vendas</h2><p>Esse cadastro cria a organização e o papel de owner em uma única transação.</p></div>
      <form action="/api/sellers" method="post" onSubmit={submit}>
        <input type="hidden" name="_csrf" defaultValue=""/>
        <input type="hidden" name="idempotency_key" defaultValue=""/>
        <label>Nome do negócio<input name="display_name" minLength={2} maxLength={120} required autoComplete="organization" placeholder="Ex.: Studio Rodrigo"/></label>
        <label>Endereço público<input name="slug" minLength={3} maxLength={63} required autoComplete="off" autoCapitalize="none" spellCheck={false} pattern="[a-z0-9]+(?:-[a-z0-9]+)*" placeholder="studio-rodrigo"/></label>
        <button className="primary-button" type="submit" disabled={status === "saving" || status === "loading"}>{status === "saving" ? "Criando…" : status === "loading" ? "Verificando…" : "Ativar meu espaço"}</button>
        <div aria-live="polite" aria-atomic="true">
          {status === "preview" ? <p className="form-note">A API está indisponível. Você pode tentar novamente sem duplicar o cadastro.</p> : null}
          {status === "unauthorized" ? <p className="form-error">Sua sessão expirou. <a href="/auth/login">Entre novamente</a>.</p> : null}
          {status === "conflict" ? <p className="form-error">Este endereço já está em uso ou sua conta já possui um espaço.</p> : null}
          {status === "invalid_slug" ? <p className="form-error">Use um endereço com letras minúsculas, números e hífens.</p> : null}
          {status === "invalid_name" ? <p className="form-error">Informe o nome sem espaços extras no início ou no fim.</p> : null}
          {status === "error" ? <p className="form-error">Não foi possível concluir. Tente novamente; a mesma tentativa não será duplicada.</p> : null}
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
