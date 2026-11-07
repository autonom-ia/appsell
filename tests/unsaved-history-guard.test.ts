import { describe, expect, it } from "vitest";

import {
  DIRTY_HISTORY_BLOCKED_EVENT,
  DIRTY_HISTORY_CONTINUE_EVENT,
  createUnsavedHistoryGuard,
  type GuardEnvironment,
  type UnsavedHistoryGuard
} from "../app/components/use-unsaved-changes";

// Um histórico falso com as duas semânticas de agendamento que importam (#612).
//
// - "webkit": `back()`/`go()` resolvem o alvo na hora da chamada, e uma
//   travessia agendada cancela a que ainda não disparou.
// - "chromium": as travessias entram numa fila e cada uma resolve o alvo
//   quando é processada.
//
// O `popstate` só chega quando o teste manda o navegador andar (`step`), como
// a tarefa separada que ele é. Entre um passo e outro a guarda pode receber
// quantas mudanças de `dirty` o teste quiser: é a oscilação do upload.
type Engine = "webkit" | "chromium";

interface Entry { document: "dashboard" | "product"; state: Record<string, unknown> | null }

class FakeBrowser {
  readonly events = new EventTarget();
  entries: Entry[] = [
    { document: "dashboard", state: null },
    { document: "product", state: { __NA: true } }
  ];
  index = 1;
  leftProduct = false;
  private scheduled: Array<{ target?: number; delta?: number }> = [];

  constructor(private readonly engine: Engine) {}

  get env(): GuardEnvironment {
    const history = {
      pushState: (state: Record<string, unknown>) => {
        this.entries = [...this.entries.slice(0, this.index + 1), { document: "product", state }];
        this.index += 1;
      },
      replaceState: (state: Record<string, unknown>) => {
        this.entries = this.entries.map((entry, i) => i === this.index ? { ...entry, state } : entry);
      },
      back: () => this.traverse(-1),
      go: (delta = 0) => this.traverse(delta)
    };
    Object.defineProperty(history, "state", { get: () => this.entries[this.index].state });
    return {
      events: this.events,
      location: { href: "https://app.test/products/p" },
      history: history as unknown as GuardEnvironment["history"]
    };
  }

  /** O voltar do usuário: imediato, com o popstate dele. */
  userBack() {
    this.arrive(this.index - 1);
  }

  hasPending() {
    return this.scheduled.length > 0;
  }

  step() {
    const next = this.scheduled.shift();
    if (!next) return;
    const target = next.target ?? this.index + (next.delta ?? 0);
    if (target < 0 || target >= this.entries.length) return;
    this.arrive(target);
  }

  settle() {
    for (let i = 0; i < 50 && this.hasPending(); i += 1) this.step();
    expect(this.hasPending()).toBe(false);
  }

  private traverse(delta: number) {
    if (this.engine === "chromium") {
      this.scheduled.push({ delta });
      return;
    }
    const target = this.index + delta;
    // Alvo fora da lista: o WebKit não agenda nada, mas cancela o que havia.
    this.scheduled = target < 0 || target >= this.entries.length ? [] : [{ target }];
  }

  private arrive(target: number) {
    const crossDocument = this.entries[target].document !== this.entries[this.index].document;
    this.index = target;
    if (crossDocument) {
      this.leftProduct = true;
      this.scheduled = [];
      return;
    }
    this.events.dispatchEvent(new Event("popstate"));
  }
}

// A guarda como era até o #612, reproduzida aqui só para provar que o
// histórico falso pega o defeito: um efeito do React por valor de `dirty`,
// com `pushState` na montagem e `history.back()` no cleanup. Fica de fora o
// que não entra na oscilação: navegação liberada e diálogo confirmado.
function legacyGuard(env: GuardEnvironment, initialDirty: boolean): UnsavedHistoryGuard {
  const { history, events } = env;
  let cleanup: (() => void) | null = null;
  const effect = (dirty: boolean) => {
    if (!dirty) return null;
    let restoring = false;
    history.pushState({ ...history.state, __appsellDirtyGuard: "token" }, "", env.location.href);
    const guardHistory = () => {
      if (restoring) {
        restoring = false;
        events.dispatchEvent(new Event(DIRTY_HISTORY_BLOCKED_EVENT));
        return;
      }
      restoring = true;
      history.go(1);
    };
    events.addEventListener("popstate", guardHistory);
    return () => {
      events.removeEventListener("popstate", guardHistory);
      history.back();
    };
  };
  let current = initialDirty;
  cleanup = effect(current);
  return {
    setDirty(next) {
      if (next === current) return;
      current = next;
      cleanup?.();
      cleanup = effect(next);
    },
    dispose() { cleanup?.(); }
  };
}

// O `dirty` do upload de um PDF de anexo: envio, verificação, política, fim.
const UPLOAD = [true, false, true, false];

/** Todas as formas de intercalar 0, 1 ou 2 passos do navegador entre as mudanças. */
function interleavings(changes: boolean[]) {
  let plans: number[][] = [[]];
  for (let i = 0; i < changes.length; i += 1) {
    plans = plans.flatMap((plan) => [0, 1, 2].map((steps) => [...plan, steps]));
  }
  return plans;
}

function runUpload(
  engine: Engine,
  createGuard: (env: GuardEnvironment, initialDirty: boolean) => UnsavedHistoryGuard,
  plan: number[]
) {
  const browser = new FakeBrowser(engine);
  const guard = createGuard(browser.env, false);
  UPLOAD.forEach((dirty, i) => {
    guard.setDirty(dirty);
    for (let s = 0; s < plan[i]; s += 1) browser.step();
  });
  browser.settle();
  return browser;
}

describe("guarda de alterações não salvas (#612)", () => {
  it("o histórico falso reproduz a saída da tela com a guarda antiga no WebKit", () => {
    const saiu = interleavings(UPLOAD).filter((plan) => runUpload("webkit", legacyGuard, plan).leftProduct);
    expect(saiu.length).toBeGreaterThan(0);
  });

  it.each<Engine>(["webkit", "chromium"])(
    "no %s, o upload nunca tira a tela do produto e não deixa sentinela para trás",
    (engine) => {
      for (const plan of interleavings(UPLOAD)) {
        const browser = runUpload(engine, createUnsavedHistoryGuard, plan);
        expect({ plan, leftProduct: browser.leftProduct }).toEqual({ plan, leftProduct: false });
        // Limpa, a tela fica na própria entrada: um voltar leva ao dashboard.
        expect({ plan, index: browser.index }).toEqual({ plan, index: 1 });
      }
    }
  );

  it.each<Engine>(["webkit", "chromium"])(
    "no %s, oscilar muitas vezes sem o navegador andar também não sai da tela",
    (engine) => {
      const browser = new FakeBrowser(engine);
      const guard = createUnsavedHistoryGuard(browser.env, false);
      for (let i = 0; i < 20; i += 1) {
        guard.setDirty(true);
        guard.setDirty(false);
        if (i % 3 === 0) browser.step();
      }
      browser.settle();
      expect(browser.leftProduct).toBe(false);
      expect(browser.index).toBe(1);
    }
  );

  it.each<Engine>(["webkit", "chromium"])(
    "no %s, o voltar com alteração pendente fica na tela e pede confirmação",
    (engine) => {
      const browser = new FakeBrowser(engine);
      let bloqueios = 0;
      browser.events.addEventListener(DIRTY_HISTORY_BLOCKED_EVENT, () => { bloqueios += 1; });
      createUnsavedHistoryGuard(browser.env, true);
      expect(browser.index).toBe(2);

      browser.userBack();
      browser.settle();
      expect(browser.leftProduct).toBe(false);
      expect(browser.index).toBe(2);
      expect(bloqueios).toBe(1);

      // Confirmar a saída sai de verdade, pulando a sentinela.
      browser.events.dispatchEvent(new Event(DIRTY_HISTORY_CONTINUE_EVENT));
      browser.settle();
      expect(browser.leftProduct).toBe(true);
      expect(browser.index).toBe(0);
    }
  );

  it("o voltar durante a saída da sentinela fica com o usuário, sem diálogo", () => {
    const browser = new FakeBrowser("chromium");
    let bloqueios = 0;
    browser.events.addEventListener(DIRTY_HISTORY_BLOCKED_EVENT, () => { bloqueios += 1; });
    const guard = createUnsavedHistoryGuard(browser.env, true);
    guard.setDirty(false);
    browser.userBack();
    browser.settle();
    expect(bloqueios).toBe(0);
    expect(browser.leftProduct).toBe(true);
  });

  it("desmontar com a tela suja tira a sentinela", () => {
    const browser = new FakeBrowser("webkit");
    const guard = createUnsavedHistoryGuard(browser.env, true);
    expect(browser.index).toBe(2);
    guard.dispose();
    browser.settle();
    expect(browser.index).toBe(1);
    expect(browser.leftProduct).toBe(false);
    expect(browser.entries[1].state).toEqual({ __NA: true });
  });

  it("navegação liberada não é segurada nem desfeita", () => {
    const browser = new FakeBrowser("webkit");
    const guard = createUnsavedHistoryGuard(browser.env, true);
    browser.events.dispatchEvent(new Event("appsell:dirty-navigation-allowed"));
    guard.setDirty(false);
    guard.dispose();
    expect(browser.hasPending()).toBe(false);
    expect(browser.index).toBe(2);
  });
});
