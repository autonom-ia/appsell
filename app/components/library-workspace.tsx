"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { isPublicSlug } from "../../lib/public-storefront";

export interface LibraryItem {
  entitlement_id: string;
  resume_lesson_id: string | null;
  seller: { id: string; slug: string; name: string };
  product: {
    id: string;
    slug: string;
    title: string;
    description: string;
    publication_id: string;
    publication_version: number;
  };
  progress_percent: number;
}

type State = "loading" | "ready" | "unauthorized" | "forbidden" | "unavailable" | "error";

const stateMessages: Record<Exclude<State, "ready">, string> = {
  loading: "Carregando sua biblioteca…",
  unauthorized: "Sua sessão expirou. Entre novamente.",
  forbidden: "Esta conta não possui acesso à biblioteca solicitada.",
  unavailable: "A biblioteca não respondeu. Tente novamente.",
  error: "Não foi possível validar os dados da biblioteca."
};

export function LibraryWorkspace() {
  const [state, setState] = useState<State>("loading");
  const [items, setItems] = useState<LibraryItem[]>([]);
  const requestRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setItems([]);
    setState("loading");
    let timedOut = false;
    const timeout = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 8_000);
    try {
      const response = await fetch("/api/library", { cache: "no-store", signal: controller.signal });
      if (controller.signal.aborted) return;
      if (response.status === 401) return setState("unauthorized");
      if (response.status === 403 || response.status === 404) return setState("forbidden");
      if (response.status === 503) return setState("unavailable");
      if (!response.ok) return setState("error");
      const payload: unknown = await response.json();
      if (!isLibraryPayload(payload)) return setState("error");
      setItems(payload.items);
      setState("ready");
    } catch {
      if (!controller.signal.aborted || timedOut) {
        setState("unavailable");
      }
    } finally {
      window.clearTimeout(timeout);
      if (requestRef.current === controller) requestRef.current = null;
    }
  }, []);

  useEffect(() => {
    const start = window.setTimeout(() => void load(), 0);
    return () => {
      window.clearTimeout(start);
      requestRef.current?.abort();
    };
  }, [load]);

  if (state !== "ready") {
    return <section className={`library-state state-${state}`} aria-live="polite" aria-busy={state === "loading"}>
      {state === "loading" ? <LibrarySkeleton/> : <p role={state === "error" || state === "unavailable" ? "alert" : undefined}>{stateMessages[state]}</p>}
      {state === "unauthorized" || state === "forbidden"
        ? <Link className="primary-button" href="/">Voltar para a entrada</Link>
        : state === "unavailable" || state === "error"
          ? <button type="button" className="primary-button" onClick={() => void load()}>Tentar novamente</button>
          : null}
    </section>;
  }

  if (!items.length) {
    return <section className="empty-state library-empty" aria-labelledby="library-empty-title"><h2 id="library-empty-title">Sua biblioteca está vazia</h2><p>Nenhum produto ativo está disponível para esta conta. Se você acabou de receber acesso, atualize a lista.</p><button type="button" className="primary-button" onClick={() => void load()}>Atualizar biblioteca</button><Link className="secondary-button" href="/">Voltar para a entrada</Link></section>;
  }

  return <div className="learner-library">{items.map((item) => {
    const started = item.progress_percent > 0;
    const completed = item.progress_percent === 100;
    const href = item.resume_lesson_id
      ? `/library/items/${encodeURIComponent(item.entitlement_id)}/lessons/${encodeURIComponent(item.resume_lesson_id)}`
      : null;
    return <article key={item.entitlement_id} className="library-product-card" aria-labelledby={`course-${item.entitlement_id}`}>
      <div className="library-product-copy">
        <p className="eyebrow">{item.seller.name}</p>
        <h2 id={`course-${item.entitlement_id}`}>{item.product.title}</h2>
        {item.product.description.trim() ? <p>{item.product.description}</p> : null}
      </div>
      <div className="course-progress">
        <span id={`progress-label-${item.entitlement_id}`}>Progresso: {item.progress_percent}%</span>
        <progress aria-labelledby={`progress-label-${item.entitlement_id}`} max={100} value={item.progress_percent}>{item.progress_percent}%</progress>
      </div>
      {href ? <Link className="primary-button library-continue" href={href}>
          {completed ? "Rever curso" : started ? "Continuar curso" : "Começar curso"}
          <span aria-hidden="true">→</span>
        </Link>
        : <div className="library-unavailable"><button className="primary-button" type="button" disabled>Conteúdo indisponível</button><p>Este produto ainda não possui uma aula publicada.</p></div>}
      <Link prefetch={false} className="library-storefront-link" href={`/s/${item.seller.slug}/${item.product.slug}`}>Ver produto e catálogo de {item.seller.name} <span aria-hidden="true">→</span></Link>
    </article>;
  })}</div>;
}

function LibrarySkeleton() {
  return <>
    <p className="sr-only">{stateMessages.loading}</p>
    <div className="library-skeleton" aria-hidden="true"><span/><span/><span/></div>
  </>;
}

export function isLibraryPayload(value: unknown): value is { items: LibraryItem[] } {
  if (!isRecord(value) || !Array.isArray(value.items)) return false;
  const entitlementIds = new Set<string>();
  return value.items.every((item) => {
    if (!isLibraryItem(item) || entitlementIds.has(item.entitlement_id)) return false;
    entitlementIds.add(item.entitlement_id);
    return true;
  });
}

function isLibraryItem(value: unknown): value is LibraryItem {
  if (!isRecord(value)) return false;
  return isUuid(value.entitlement_id) && (value.resume_lesson_id === null || isUuid(value.resume_lesson_id)) &&
    isSeller(value.seller) && isProduct(value.product) &&
    Number.isInteger(value.progress_percent) && typeof value.progress_percent === "number" &&
    value.progress_percent >= 0 && value.progress_percent <= 100;
}

function isProduct(value: unknown) {
  return isRecord(value) && isNonEmptyString(value.id) && isPublicSlug(value.slug) &&
    isNonEmptyString(value.title) && isNonEmptyString(value.publication_id) && typeof value.description === "string" &&
    Number.isInteger(value.publication_version) && (value.publication_version as number) > 0;
}

function isSeller(value: unknown) {
  return isRecord(value) && isNonEmptyString(value.id) && isPublicSlug(value.slug) && isNonEmptyString(value.name);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isUuid(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
