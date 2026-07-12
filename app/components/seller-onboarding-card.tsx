"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

interface Seller {
  id: string;
  slug: string;
  display_name: string;
  role: string;
}

type SellerStatus = "loading" | "idle" | "saving" | "created_pending_refresh" | "created_unauthorized" | "refreshing_created" | "preview" | "unauthorized" | "conflict" | "invalid_slug" | "invalid_name" | "error";

export function SellerOnboardingCard() {
  const [sellers, setSellers] = useState<Seller[] | null>(null);
  const [status, setStatus] = useState<SellerStatus>("loading");
  const [creationSucceeded, setCreationSucceeded] = useState(false);
  const idempotencyKey = useRef<string | null>(null);
  const creationSucceededRef = useRef(false);
  const mounted = useRef(false);
  const loadVersion = useRef(0);
  const loadRequest = useRef<{ controller: AbortController; version: number } | null>(null);
  const submitRequest = useRef<AbortController | null>(null);
  const submissionInFlight = useRef(false);

  const loadSellers = useCallback(async (): Promise<boolean> => {
    loadRequest.current?.controller.abort();
    const controller = new AbortController();
    const version = ++loadVersion.current;
    loadRequest.current = { controller, version };
    const isCurrent = () => mounted.current && loadRequest.current?.version === version;
    const timeout = setTimeout(() => controller.abort(), 5_000);
    try {
      const response = await fetch("/api/sellers", {
        cache: "no-store",
        signal: controller.signal
      });
      if (!isCurrent()) return false;
      if (!response.ok) {
        if (creationSucceededRef.current && response.status === 401) setStatus("created_unauthorized");
        else if (creationSucceededRef.current) setStatus("created_pending_refresh");
        else if (response.status === 401) setStatus("unauthorized");
        else if (response.status === 503) setStatus("preview");
        else setStatus("error");
        return false;
      }
      const payload = await response.json() as { sellers?: Seller[] };
      if (!isCurrent()) return false;
      const nextSellers = Array.isArray(payload.sellers) ? payload.sellers : [];
      setSellers(nextSellers);
      if (creationSucceededRef.current && nextSellers.length === 0) {
        setStatus("created_pending_refresh");
        return false;
      }
      creationSucceededRef.current = false;
      idempotencyKey.current = null;
      setStatus("idle");
      return true;
    } catch {
      if (isCurrent()) setStatus(creationSucceededRef.current ? "created_pending_refresh" : "error");
      return false;
    } finally {
      clearTimeout(timeout);
      if (loadRequest.current?.version === version) loadRequest.current = null;
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    const start = window.setTimeout(() => void loadSellers(), 0);
    return () => {
      mounted.current = false;
      window.clearTimeout(start);
      loadRequest.current?.controller.abort();
      loadRequest.current = null;
      submitRequest.current?.abort();
      submitRequest.current = null;
    };
  }, [loadSellers]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (creationSucceededRef.current || submissionInFlight.current) return;
    submissionInFlight.current = true;
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
    submitRequest.current = controller;
    const timeout = setTimeout(() => controller.abort(), 8_000);
    try {
      const response = await fetch("/api/sellers", {
        method: "POST",
        body: data,
        signal: controller.signal
      });
      if (!mounted.current || submitRequest.current !== controller) return;
      if (!response.ok) {
        if (response.status === 401) setStatus("unauthorized");
        else if (response.status === 409) setStatus("conflict");
        else if (response.status === 422) {
          const payload = await response.json().catch(() => null) as { field?: string } | null;
          if (!mounted.current || submitRequest.current !== controller) return;
          const field = payload?.field === "display_name" ? "display_name" : "slug";
          setStatus(field === "display_name" ? "invalid_name" : "invalid_slug");
          const input = form.elements.namedItem(field);
          if (input instanceof HTMLInputElement) input.focus();
        } else if (response.status === 503) setStatus("preview");
        else setStatus("error");
        return;
      }
      creationSucceededRef.current = true;
      form.reset();
      setCreationSucceeded(true);
      setStatus("created_pending_refresh");
      if (await loadSellers() && mounted.current) window.dispatchEvent(new Event("appsell:sellers-changed"));
    } catch {
      if (mounted.current && submitRequest.current === controller) setStatus("error");
    } finally {
      clearTimeout(timeout);
      if (submitRequest.current === controller) submitRequest.current = null;
      submissionInFlight.current = false;
    }
  }

  async function refreshCreatedSeller() {
    if (!mounted.current) return;
    setStatus("refreshing_created");
    if (await loadSellers() && mounted.current) window.dispatchEvent(new Event("appsell:sellers-changed"));
  }

  if (sellers?.length) {
    return <section className="seller-onboarding seller-ready"><div><p className="eyebrow">Espaços ativos</p><h2>{sellers.length === 1 ? "Seu espaço está pronto" : `${sellers.length} espaços disponíveis`}</h2><p>Escolha abaixo qual espaço deseja administrar.</p></div><ul>{sellers.map((seller) => <li key={seller.id}><strong>{seller.display_name}</strong><span>@{seller.slug} · {sellerRoleLabel(seller.role)}</span></li>)}</ul></section>;
  }

  return (
    <section className="seller-onboarding">
      <div><p className="eyebrow">Ativação do produtor</p><h2>Crie seu espaço de vendas</h2><p>Use este espaço para organizar seus produtos, cursos e alunos.</p></div>
      <form action="/api/sellers" method="post" onSubmit={submit}>
        <input type="hidden" name="_csrf" defaultValue=""/>
        <input type="hidden" name="idempotency_key" defaultValue=""/>
        <label>Nome do negócio<input name="display_name" minLength={2} maxLength={120} required autoComplete="organization" placeholder="Ex.: Studio Rodrigo…" disabled={creationSucceeded} aria-invalid={status === "invalid_name"} aria-describedby={status === "invalid_name" ? "seller-name-error" : undefined}/>{status === "invalid_name" ? <span id="seller-name-error" className="form-error">Informe o nome sem espaços extras no início ou no fim.</span> : null}</label>
        <label>Endereço público<input name="slug" minLength={3} maxLength={63} required autoComplete="off" autoCapitalize="none" spellCheck={false} pattern="[a-z0-9]+(?:-[a-z0-9]+)*" placeholder="studio-rodrigo…" disabled={creationSucceeded} aria-invalid={status === "invalid_slug"} aria-describedby={status === "invalid_slug" ? "seller-slug-error" : undefined}/>{status === "invalid_slug" ? <span id="seller-slug-error" className="form-error">Use letras minúsculas, números e hífens.</span> : null}</label>
        <button className="primary-button" type="submit" disabled={status === "saving" || status === "loading" || creationSucceeded}>{creationSucceeded ? "Espaço criado" : status === "saving" ? "Criando…" : status === "loading" ? "Verificando…" : "Ativar meu espaço"}</button>
        <div aria-live="polite" aria-atomic="true">
          {status === "preview" ? <p className="form-note">A API está indisponível. Você pode tentar novamente sem duplicar o cadastro.</p> : null}
          {status === "created_pending_refresh" ? <p className="form-note">Espaço criado, mas não foi possível atualizar a lista. Não envie o cadastro novamente.</p> : null}
          {status === "created_unauthorized" ? <p className="form-error">Espaço criado, mas sua sessão expirou antes de atualizar a lista. Não envie o cadastro novamente. <a href="/auth/login">Entre novamente</a>.</p> : null}
          {status === "refreshing_created" ? <p className="form-note">Espaço criado. Atualizando a lista…</p> : null}
          {status === "unauthorized" ? <p className="form-error">Sua sessão expirou. <a href="/auth/login">Entre novamente</a>.</p> : null}
          {status === "conflict" ? <p className="form-error">Este endereço já está em uso ou sua conta já possui um espaço.</p> : null}
          {status === "error" ? <p className="form-error">Não foi possível concluir. Tente novamente; a mesma tentativa não será duplicada.</p> : null}
          {creationSucceeded && (status === "created_pending_refresh" || status === "refreshing_created") ? <button type="button" className="secondary-button" disabled={status === "refreshing_created"} onClick={() => void refreshCreatedSeller()}>{status === "refreshing_created" ? "Atualizando lista…" : "Atualizar lista de espaços"}</button> : null}
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

function sellerRoleLabel(role: string) {
  if (role === "owner" || role === "seller_owner") return "proprietário";
  if (role === "seller_admin") return "administrador";
  return "membro da equipe";
}
