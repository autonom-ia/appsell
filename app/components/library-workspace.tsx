"use client";

import Link from "next/link";
import { NEW_STORE_PATH } from "./app-shell";
import { canOpenFirstStore, useShellProfile } from "./shell-profile-context";
import { useCallback, useEffect, useRef, useState } from "react";
import { parseBuyerProductsPayload, type BuyerProductItem } from "../../lib/buyer-products";

export type LibraryItem = BuyerProductItem;

type State = "loading" | "ready" | "unauthorized" | "forbidden" | "unavailable" | "error";

const stateMessages: Record<Exclude<State, "ready">, string> = {
  loading: "Carregando sua biblioteca…",
  unauthorized: "Sua sessão expirou. Entre novamente.",
  forbidden: "Esta conta não possui acesso à biblioteca solicitada.",
  unavailable: "A biblioteca não respondeu. Tente novamente.",
  error: "Não foi possível validar os dados da biblioteca."
};

export function LibraryWorkspace({ demoCheckoutEnabled = false }: { demoCheckoutEnabled?: boolean }) {
  const profile = useShellProfile();
  // Só oferece criar loja a quem já poderia: identidade real resolvida e ainda
  // sem nenhuma. Persona demo e sessão não resolvida ficam de fora.
  const showStoreInvite = canOpenFirstStore(profile);
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
      const response = await fetch("/api/me/products", { cache: "no-store", signal: controller.signal });
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
    // Biblioteca vazia e sem loja é, quase sempre, quem entrou para vender e
    // parou no meio. A saída precisa estar aqui: é a tela em que ele cai.
    return <section className="empty-state library-empty" aria-labelledby="library-empty-title"><h2 id="library-empty-title">Sua biblioteca está vazia</h2><p>Nenhum produto ativo está disponível para esta conta.{demoCheckoutEnabled ? " Conclua a compra demo para liberar o curso." : ""}</p>{demoCheckoutEnabled ? <Link className="primary-button commercial-button" href="/checkout">Comprar com cupom demo</Link> : null}{showStoreInvite ? <Link className="primary-button" href={NEW_STORE_PATH}>Criar minha loja</Link> : null}<button type="button" className="secondary-button" onClick={() => void load()}>Atualizar biblioteca</button></section>;
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
      {/* Antes daqui saía um acordeão que listava os outros produtos como
          texto morto, sem link nenhum: prometia navegação e entregava uma
          lista de onde não se saía. A loja do produtor já é essa vitrine. */}
      <Link className="secondary-button library-store-link" href={`/s/${encodeURIComponent(item.seller.slug)}`}>
        Ver a loja de {item.seller.name}
      </Link>
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
  return parseBuyerProductsPayload(value) !== null;
}
