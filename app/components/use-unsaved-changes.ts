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
  timers: { set(callback: () => void, ms: number): number; clear(id: number): void };
}

// Quanto a guarda espera o `popstate` do `go(1)` que devolve a sentinela.
// Uma travessia na mesma página leva milissegundos; sem entrada à frente ele
// nunca chega.
export const RESTORE_DEADLINE_MS = 1_000;

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
  const { history, location, events, timers } = env;
  // A marca desta guarda na sentinela. O caminho normal conta as travessias
  // que a guarda pediu (o `router.replace` do Next pode reescrever o estado da
  // entrada); a marca só decide quando a conta não fecha: o `go(1)` que não
  // chegou e a volta depois de uma navegação liberada.
  const token = crypto.randomUUID();
  let dirty = initialDirty;
  let sentinel = false;
  let pending: PendingTraversal = null;
  let leavingUrl = "";
  let restoringTimer: ReturnType<GuardEnvironment["timers"]["set"]> | null = null;
  let allowNavigation = false;
  let disposed = false;

  const onSentinel = () => (history.state as { __appsellDirtyGuard?: unknown } | null)?.__appsellDirtyGuard === token;

  const reconcile = () => {
    if (pending !== null || allowNavigation || disposed) return;
    if (dirty && !sentinel) {
      history.pushState({ ...history.state, __appsellDirtyGuard: token }, "", location.href);
      sentinel = true;
      return;
    }
    if (!dirty && sentinel) {
      pending = "leaving";
      leavingUrl = location.href;
      history.back();
    }
  };

  const clearRestoringTimer = () => {
    if (restoringTimer !== null) timers.clear(restoringTimer);
    restoringTimer = null;
  };

  const onPopState = () => {
    if (pending === "leaving") {
      pending = null;
      sentinel = false;
      // A URL pode ter mudado com a sentinela no topo (`router.replace` de
      // troca de seção ou de loja). A entrada que sobra fica com ela.
      if (location.href !== leavingUrl) {
        history.replaceState(history.state, "", leavingUrl);
      }
      reconcile();
      return;
    }
    if (pending === "restoring") {
      clearRestoringTimer();
      pending = null;
      sentinel = true;
      // Só pede confirmação se a tela ainda está suja ao voltar à sentinela.
      if (dirty) events.dispatchEvent(new Event(DIRTY_HISTORY_BLOCKED_EVENT));
      reconcile();
      return;
    }
    if (allowNavigation || !sentinel) return;
    // O navegador saiu da sentinela sem a guarda pedir: o voltar do usuário,
    // ou uma navegação por fragmento empilhada em cima dela. Com a tela suja,
    // a guarda tenta devolver a sentinela; limpa, a travessia é do usuário.
    if (dirty) {
      pending = "restoring";
      history.go(1);
      // Sem entrada à frente (fragmento), o `go(1)` não chega e não há
      // `popstate`. O prazo devolve a guarda ao estado real em vez de
      // deixá-la parada até desmontar.
      restoringTimer = timers.set(() => {
        restoringTimer = null;
        if (pending !== "restoring" || disposed) return;
        pending = null;
        sentinel = onSentinel();
        reconcile();
      }, RESTORE_DEADLINE_MS);
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
      // Navegação liberada que não desmontou a tela (o mesmo editor em outra
      // URL) já passou. Uma alteração nova volta a ser protegida, e a
      // sentinela antiga ficou para trás no histórico.
      if (next && !dirty && allowNavigation) {
        allowNavigation = false;
        sentinel = onSentinel();
        pending = null;
      }
      dirty = next;
      reconcile();
    },
    dispose() {
      disposed = true;
      clearRestoringTimer();
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
    const guard = createUnsavedHistoryGuard({
      history,
      location: window.location,
      events: window,
      timers: { set: (callback, ms) => window.setTimeout(callback, ms), clear: (id) => window.clearTimeout(id) }
    }, dirtyRef.current);
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
