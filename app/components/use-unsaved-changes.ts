"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

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
  history: Pick<History, "state" | "length" | "pushState" | "replaceState" | "back" | "go">;
  location: Pick<Location, "href">;
  events: Pick<EventTarget, "addEventListener" | "removeEventListener" | "dispatchEvent">;
  timers: { set(callback: () => void, ms: number): number; clear(id: number): void };
  // O router do app. A URL que mudou com a sentinela no topo volta pelas mãos
  // dele: escrita por fora, o Next a sobrescreve ao terminar a própria
  // restauração do `popstate`, e a tela fica na etapa anterior (#615, ponto C).
  router: { replace(url: string): void };
}

// Quanto a guarda espera o `popstate` do `go(1)` que devolve a sentinela.
// Uma travessia na mesma página leva milissegundos; sem entrada à frente ele
// nunca chega.
export const RESTORE_DEADLINE_MS = 1_000;

// Quanto uma navegação pedida por `whenSettled` espera a guarda assentar. Se
// o `popstate` da travessia dela nunca chegar, a navegação segue mesmo assim:
// o vendedor não pode ficar preso no editor.
export const SETTLE_DEADLINE_MS = 1_000;

export interface UnsavedHistoryGuard {
  setDirty(dirty: boolean): void;
  /**
   * Roda `navigate` quando o histórico estiver assentado: sem travessia da
   * guarda em voo e sem sentinela por sair. Uma navegação do app feita
   * durante o `history.back()` da guarda é desfeita pelo `popstate` dele.
   */
  whenSettled(navigate: () => void): void;
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
  // que a guarda pediu, porque `router.push` e `router.replace` do Next
  // gravam o estado da entrada sem preservar o que não é dele (só a travessia
  // preserva). A marca decide quando a conta não fecha: o `go(1)` que não
  // chegou ou chegou tarde, e a volta depois de uma navegação liberada.
  const token = crypto.randomUUID();
  let dirty = initialDirty;
  let sentinel = false;
  // A sentinela está logo abaixo de uma entrada que o próprio vendedor
  // empilhou na mesma página (link de fragmento). Ela continua protegendo: o
  // voltar dele cai nela antes de sair.
  let sentinelBelow = false;
  // Quantas entradas o histórico tinha da última vez que a guarda olhou. Um
  // `popstate` com mais entradas não é voltar: é uma entrada nova na página.
  let knownLength = history.length;
  let pending: PendingTraversal = null;
  let leavingUrl = "";
  let restoringTimer: ReturnType<GuardEnvironment["timers"]["set"]> | null = null;
  let allowNavigation = false;
  // Onde a tela estava quando a navegação foi liberada. Enquanto o navegador
  // não sai dela, a navegação ainda está em voo e não pode ser desfeita.
  let allowedFromUrl = "";
  // A navegação foi liberada com a sentinela no topo. Se a marca some sem a
  // URL mudar, o Next gravou a mesma URL por cima dela: a navegação chegou.
  let allowedOnSentinel = false;
  let disposed = false;
  // Navegações do app que esperam o histórico assentar (`whenSettled`).
  let waiting: Array<() => void> = [];
  let settleTimer: ReturnType<GuardEnvironment["timers"]["set"]> | null = null;

  const onSentinel = () => (history.state as { __appsellDirtyGuard?: unknown } | null)?.__appsellDirtyGuard === token;

  // A tela empilhou uma entrada por cima da sentinela sem `popstate` (o
  // `pushState` de abrir uma aula, #649). O `back()` da guarda tiraria essa
  // entrada, e não a sentinela: a pessoa seria levada para a tela de antes.
  const pushedOverSentinel = () => history.length !== knownLength;

  const settled = () => pending === null && (allowNavigation || disposed || dirty || !sentinel);

  const flushWaiting = () => {
    if (!settled() || waiting.length === 0) return;
    runWaiting();
  };

  const runWaiting = () => {
    if (settleTimer !== null) timers.clear(settleTimer);
    settleTimer = null;
    const ready = waiting;
    waiting = [];
    ready.forEach((navigate) => navigate());
  };

  const adjust = () => {
    if (pending !== null || allowNavigation || disposed) return;
    if (dirty && !sentinel) {
      if (sentinelBelow) return;
      history.pushState({ ...history.state, __appsellDirtyGuard: token }, "", location.href);
      knownLength = history.length;
      sentinel = true;
      return;
    }
    if (!dirty && sentinel) {
      if (pushedOverSentinel()) {
        // A sentinela fica para trás; se o voltar pousar nela, `onPopState`
        // a reconhece e a atravessa.
        sentinel = false;
        sentinelBelow = false;
        knownLength = history.length;
        return;
      }
      pending = "leaving";
      leavingUrl = location.href;
      history.back();
    }
  };

  const reconcile = () => {
    adjust();
    flushWaiting();
  };

  const clearRestoringTimer = () => {
    if (restoringTimer !== null) timers.clear(restoringTimer);
    restoringTimer = null;
  };

  const onPopState = () => {
    const grew = history.length > knownLength;
    knownLength = history.length;
    if (pending === "leaving") {
      pending = null;
      sentinel = false;
      sentinelBelow = false;
      // A URL pode ter mudado com a sentinela no topo (`router.replace` de
      // troca de seção ou de loja). A entrada que sobra fica com ela, e quem a
      // grava é o router: ele também troca a tela que acabou de restaurar.
      if (location.href !== leavingUrl) env.router.replace(leavingUrl);
      reconcile();
      return;
    }
    if (pending === "restoring") {
      clearRestoringTimer();
      pending = null;
      sentinel = true;
      sentinelBelow = false;
      // Só pede confirmação se a tela ainda está suja ao voltar à sentinela.
      if (dirty) events.dispatchEvent(new Event(DIRTY_HISTORY_BLOCKED_EVENT));
      reconcile();
      return;
    }
    if (allowNavigation) return;
    // Pousou na própria sentinela sem a guarda pedir: o voltar de um
    // fragmento, ou o `go(1)` que chegou depois do prazo. É chegada, não saída.
    if (onSentinel()) {
      sentinel = true;
      sentinelBelow = false;
      reconcile();
      return;
    }
    if (!sentinel) {
      sentinelBelow = false;
      return;
    }
    // Entrada nova em cima da sentinela (link de fragmento): é do vendedor, na
    // mesma página. A sentinela fica embaixo, protegendo, e nenhuma outra
    // entra por cima para enterrá-la.
    if (grew) {
      sentinel = false;
      sentinelBelow = true;
      return;
    }
    // O navegador saiu da sentinela sem a guarda pedir: o voltar do usuário.
    // Com a tela suja, a guarda tenta devolver a sentinela; limpa, a travessia
    // é do usuário.
    if (dirty) {
      pending = "restoring";
      clearRestoringTimer();
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
  const allow = () => {
    allowNavigation = true;
    allowedFromUrl = location.href;
    allowedOnSentinel = sentinel && onSentinel();
  };
  const continueHistory = () => {
    allowNavigation = true;
    allowedFromUrl = location.href;
    allowedOnSentinel = false;
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
      // URL) já chegou: uma alteração nova volta a ser protegida, e a
      // sentinela antiga ficou para trás no histórico. Ainda na URL de onde
      // saiu, a navegação está em voo, e rearmar agora faria o desmontar
      // devolver o vendedor à tela que ele acabou de deixar.
      const moved = location.href !== allowedFromUrl;
      const rewritten = !moved && allowedOnSentinel && !onSentinel();
      if (next && !dirty && allowNavigation && (moved || rewritten)) {
        allowNavigation = false;
        clearRestoringTimer();
        pending = null;
        sentinelBelow = false;
        knownLength = history.length;
        // A mesma URL gravada por cima da sentinela: a entrada é a mesma de
        // antes e volta a ser a sentinela, sem empilhar outra.
        if (rewritten) {
          history.replaceState({ ...history.state, __appsellDirtyGuard: token }, "", location.href);
        }
        sentinel = onSentinel();
      }
      dirty = next;
      reconcile();
    },
    whenSettled(navigate) {
      waiting = [...waiting, navigate];
      flushWaiting();
      if (waiting.length > 0 && settleTimer === null) {
        settleTimer = timers.set(() => {
          settleTimer = null;
          runWaiting();
        }, SETTLE_DEADLINE_MS);
      }
    },
    dispose() {
      disposed = true;
      clearRestoringTimer();
      events.removeEventListener("popstate", onPopState);
      events.removeEventListener("beforeunload", warnBeforeUnload);
      events.removeEventListener(DIRTY_NAVIGATION_ALLOWED_EVENT, allow);
      events.removeEventListener(DIRTY_HISTORY_CONTINUE_EVENT, continueHistory);
      // Desmontar sem navegação liberada deixaria a sentinela para trás. Com
      // ela embaixo de um fragmento, fica: tirá-la custaria a entrada que o
      // vendedor empilhou, e o voltar dele só a atravessa na mesma página. A
      // URL pode ter mudado com ela no topo, então a entrada que sobra recebe
      // a URL atual.
      if (sentinel && pending === null && !allowNavigation && !pushedOverSentinel()) {
        const targetUrl = location.href;
        events.addEventListener("popstate", () => {
          const { __appsellDirtyGuard: _guard, ...rest } = history.state ?? {};
          void _guard;
          history.replaceState(rest, "", targetUrl);
        }, { once: true });
        history.back();
      }
      // A tela saiu: quem esperava navega agora, sem guarda para desfazer.
      runWaiting();
    }
  };
}

/**
 * Protege a tela enquanto `dirty` e devolve como navegar dentro dela sem
 * brigar com a guarda: a navegação pedida espera o React aplicar o estado do
 * mesmo evento (o descarte, por exemplo) e a guarda tirar a sentinela.
 */
export function useUnsavedChanges(dirty: boolean): (navigate: () => void) => void {
  const router = useRouter();
  const routerRef = useRef(router);
  const dirtyRef = useRef(dirty);
  const guardRef = useRef<UnsavedHistoryGuard | null>(null);
  const requestsRef = useRef<Array<() => void>>([]);
  const [requestTick, setRequestTick] = useState(0);

  useEffect(() => {
    const guard = createUnsavedHistoryGuard({
      history,
      location: window.location,
      events: window,
      timers: { set: (callback, ms) => window.setTimeout(callback, ms), clear: (id) => window.clearTimeout(id) },
      router: { replace: (url) => routerRef.current.replace(url, { scroll: false }) }
    }, dirtyRef.current);
    guardRef.current = guard;
    return () => {
      guardRef.current = null;
      guard.dispose();
      window.dispatchEvent(new CustomEvent(DIRTY_STATE_EVENT, { detail: { dirty: false } }));
    };
  }, []);

  useEffect(() => {
    routerRef.current = router;
  }, [router]);

  useEffect(() => {
    dirtyRef.current = dirty;
    window.dispatchEvent(new CustomEvent(DIRTY_STATE_EVENT, { detail: { dirty } }));
    guardRef.current?.setDirty(dirty);
  }, [dirty]);

  // Depois do efeito de `dirty`: no mesmo commit, a guarda já sabe se a tela
  // ficou limpa e se a sentinela está saindo.
  useEffect(() => {
    if (requestsRef.current.length === 0) return;
    const ready = requestsRef.current;
    requestsRef.current = [];
    const guard = guardRef.current;
    ready.forEach((navigate) => (guard ? guard.whenSettled(navigate) : navigate()));
  }, [requestTick]);

  return useCallback((navigate: () => void) => {
    requestsRef.current = [...requestsRef.current, navigate];
    setRequestTick((tick) => tick + 1);
  }, []);
}
