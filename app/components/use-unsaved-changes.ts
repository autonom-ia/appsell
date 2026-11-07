"use client";

import { useEffect, useRef } from "react";

export const DIRTY_STATE_EVENT = "appsell:dirty-state";
export const DIRTY_NAVIGATION_REQUEST_EVENT = "appsell:dirty-navigation-request";
export const DIRTY_HISTORY_BLOCKED_EVENT = "appsell:dirty-history-blocked";
export const DIRTY_HISTORY_CONTINUE_EVENT = "appsell:dirty-history-continue";
export const DIRTY_NAVIGATION_ALLOWED_EVENT = "appsell:dirty-navigation-allowed";

export function allowUnsavedNavigation() {
  window.dispatchEvent(new Event(DIRTY_NAVIGATION_ALLOWED_EVENT));
}

// O pedaço do navegador que a guarda usa. Em teste entra um histórico falso
// com a semântica de agendamento de cada motor (tests/unsaved-history-guard.test.ts).
export interface GuardEnvironment {
  history: Pick<History, "state" | "pushState" | "replaceState" | "back" | "go">;
  location: Pick<Location, "href">;
  events: Pick<EventTarget, "addEventListener" | "removeEventListener" | "dispatchEvent">;
}

export interface UnsavedHistoryGuard {
  setDirty(dirty: boolean): void;
  dispose(): void;
}

// A travessia que a própria guarda pediu e cujo `popstate` ainda não chegou.
// `leaving` tira a sentinela quando a tela fica limpa; `restoring` a devolve
// quando o usuário voltou com alteração pendente.
type PendingTraversal = "leaving" | "restoring" | null;

/**
 * Protege o voltar do navegador enquanto há alteração não salva.
 *
 * Com a tela suja, uma entrada extra (a sentinela) fica no topo do histórico,
 * com a mesma URL. O voltar do usuário cai nela, a guarda devolve a entrada e
 * pede confirmação. Com a tela limpa, a sentinela sai, e um único voltar leva
 * para a página anterior.
 *
 * Antes (#612), cada mudança de `dirty` montava e desmontava a guarda
 * inteira: o cleanup fazia `history.back()` e o efeito seguinte,
 * `pushState`. O `history.back()` é assíncrono, e o `popstate` dele chegava
 * ao listener do efeito seguinte, que o tomava por gesto do usuário e pedia
 * `go(1)`. No WebKit uma navegação de histórico agendada cancela a anterior:
 * o `back()` seguinte cancelava o `go(1)` e o vendedor saía da tela do
 * produto. Num upload de anexo o `dirty` oscila duas vezes em menos de 100 ms.
 *
 * Agora a guarda vive a montagem inteira e as travessias dela andam em série:
 * só uma em voo, e o `popstate` dela é reconhecido como dela. A sentinela
 * entra e sai pelo estado atual (`reconcile`), nunca por evento, então
 * oscilar rápido só adia o acerto.
 */
export function createUnsavedHistoryGuard(env: GuardEnvironment, initialDirty: boolean): UnsavedHistoryGuard {
  const { history, location, events } = env;
  let dirty = initialDirty;
  let sentinel = false;
  let pending: PendingTraversal = null;
  let allowNavigation = false;
  let disposed = false;

  const reconcile = () => {
    if (pending !== null || allowNavigation || disposed) return;
    if (dirty && !sentinel) {
      history.pushState({ ...history.state, __appsellDirtyGuard: true }, "", location.href);
      sentinel = true;
      return;
    }
    if (!dirty && sentinel) {
      pending = "leaving";
      history.back();
    }
  };

  const onPopState = () => {
    if (pending === "leaving") {
      pending = null;
      sentinel = false;
      reconcile();
      return;
    }
    if (pending === "restoring") {
      pending = null;
      sentinel = true;
      events.dispatchEvent(new Event(DIRTY_HISTORY_BLOCKED_EVENT));
      reconcile();
      return;
    }
    if (allowNavigation || !sentinel) return;
    // O usuário saiu da sentinela. Com a tela suja, ela volta e o diálogo
    // decide; limpa, ela já estava de saída e a travessia é dele.
    if (dirty) {
      pending = "restoring";
      history.go(1);
      return;
    }
    sentinel = false;
  };
  const warnBeforeUnload = (event: Event) => {
    if (dirty && !allowNavigation) event.preventDefault();
  };
  const allow = () => { allowNavigation = true; };
  const continueHistory = () => {
    allowNavigation = true;
    // A sentinela e a entrada da própria tela: dois passos para sair.
    history.go(sentinel ? -2 : -1);
  };

  events.addEventListener("popstate", onPopState);
  events.addEventListener("beforeunload", warnBeforeUnload);
  events.addEventListener(DIRTY_NAVIGATION_ALLOWED_EVENT, allow);
  events.addEventListener(DIRTY_HISTORY_CONTINUE_EVENT, continueHistory);
  reconcile();

  return {
    setDirty(next) {
      dirty = next;
      reconcile();
    },
    dispose() {
      disposed = true;
      events.removeEventListener("popstate", onPopState);
      events.removeEventListener("beforeunload", warnBeforeUnload);
      events.removeEventListener(DIRTY_NAVIGATION_ALLOWED_EVENT, allow);
      events.removeEventListener(DIRTY_HISTORY_CONTINUE_EVENT, continueHistory);
      // Desmontar sem navegação liberada deixaria a sentinela para trás. A
      // URL pode ter mudado com ela no topo, então a entrada que sobra recebe
      // a URL atual.
      if (sentinel && pending === null && !allowNavigation) {
        const targetUrl = location.href;
        events.addEventListener("popstate", () => {
          const { __appsellDirtyGuard: _guard, ...rest } = history.state ?? {};
          void _guard;
          history.replaceState(rest, "", targetUrl);
        }, { once: true });
        history.back();
      }
    }
  };
}

export function useUnsavedChanges(dirty: boolean) {
  const dirtyRef = useRef(dirty);
  const guardRef = useRef<UnsavedHistoryGuard | null>(null);

  useEffect(() => {
    const guard = createUnsavedHistoryGuard({ history, location: window.location, events: window }, dirtyRef.current);
    guardRef.current = guard;
    return () => {
      guardRef.current = null;
      guard.dispose();
      window.dispatchEvent(new CustomEvent(DIRTY_STATE_EVENT, { detail: { dirty: false } }));
    };
  }, []);

  useEffect(() => {
    dirtyRef.current = dirty;
    window.dispatchEvent(new CustomEvent(DIRTY_STATE_EVENT, { detail: { dirty } }));
    guardRef.current?.setDirty(dirty);
  }, [dirty]);
}
