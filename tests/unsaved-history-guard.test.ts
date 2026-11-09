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

interface Entry { document: "dashboard" | "product"; state: Record<string, unknown> | null; url: string }

const PRODUCT_URL = "https://app.test/products/p";

class FakeBrowser {
  readonly events = new EventTarget();
  entries: Entry[] = [
    { document: "dashboard", state: null, url: "https://app.test/dashboard" },
    { document: "product", state: { __NA: true }, url: PRODUCT_URL }
  ];
  index = 1;
  leftProduct = false;
  private scheduled: Array<{ target?: number; delta?: number }> = [];
  private timers = new Map<number, () => void>();
  /** URLs que a guarda pediu ao router do app. */
  readonly routerReplaces: string[] = [];
  private nextTimer = 1;

  constructor(private readonly engine: Engine) {}

  get env(): GuardEnvironment {
    const history = {
      pushState: (state: Record<string, unknown>, _title: string, url?: string) => {
        this.push({ document: "product", state, url: url ?? this.url });
      },
      replaceState: (state: Record<string, unknown>, _title: string, url?: string) => {
        this.entries = this.entries.map((entry, i) => i === this.index ? { ...entry, state, url: url ?? entry.url } : entry);
      },
      back: () => this.traverse(-1),
      go: (delta = 0) => this.traverse(delta)
    };
    Object.defineProperty(history, "state", { get: () => this.entries[this.index].state });
    Object.defineProperty(history, "length", { get: () => this.entries.length });
    const location = {};
    Object.defineProperty(location, "href", { get: () => this.url });
    return {
      events: this.events,
      location: location as GuardEnvironment["location"],
      history: history as unknown as GuardEnvironment["history"],
      router: { replace: (url) => { this.routerReplaces.push(url); this.routerReplace(url); } },
      timers: {
        set: (callback) => {
          const id = this.nextTimer++;
          this.timers.set(id, callback);
          return id;
        },
        clear: (id) => { this.timers.delete(id); }
      }
    };
  }

  get url() {
    return this.entries[this.index].url;
  }

  /** O voltar do usuário: imediato, com o popstate dele. */
  userBack() {
    this.arrive(this.index - 1);
  }

  /** Link para um fragmento da própria página: entrada nova, com popstate. */
  fragment(hash: string) {
    this.push({ document: "product", state: null, url: `${this.url.split("#")[0]}#${hash}` });
    this.events.dispatchEvent(new Event("popstate"));
  }

  /** `router.replace` do Next: troca a URL e reescreve o estado da entrada. */
  routerReplace(url: string) {
    this.entries = this.entries.map((entry, i) => i === this.index ? { ...entry, state: { __NA: true }, url } : entry);
  }

  /** `router.push` do Next: entrada nova na mesma página, sem popstate. */
  routerPush(url: string) {
    // Mesma URL: o Next não empilha, troca o estado da entrada (e com ele a
    // marca da guarda), como no `HistoryUpdater` do app router.
    if (url === this.url) {
      this.routerReplace(url);
      return;
    }
    this.push({ document: "product", state: { __NA: true }, url });
  }

  /** O prazo dos temporizadores pendentes se esgota. */
  elapse() {
    const pending = [...this.timers.values()];
    this.timers.clear();
    pending.forEach((callback) => callback());
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

  private push(entry: Entry) {
    this.entries = [...this.entries.slice(0, this.index + 1), entry];
    this.index += 1;
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
    whenSettled(navigate) { navigate(); },
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

  // Tela limpa: o voltar é do usuário e nada se perde. No Chromium a volta da
  // guarda segue na fila e o usuário chega à página anterior; no WebKit ela
  // tinha alvo fixo na entrada da tela, e o usuário precisa de um segundo
  // voltar. Nos dois, sem diálogo.
  it.each<[Engine, { leftProduct: boolean; index: number }]>([
    ["chromium", { leftProduct: true, index: 0 }],
    ["webkit", { leftProduct: false, index: 1 }]
  ])("no %s, o voltar durante a saída da sentinela fica com o usuário, sem diálogo", (engine, esperado) => {
    const browser = new FakeBrowser(engine);
    let bloqueios = 0;
    browser.events.addEventListener(DIRTY_HISTORY_BLOCKED_EVENT, () => { bloqueios += 1; });
    const guard = createUnsavedHistoryGuard(browser.env, true);
    guard.setDirty(false);
    browser.userBack();
    browser.settle();
    expect(bloqueios).toBe(0);
    expect({ leftProduct: browser.leftProduct, index: browser.index }).toEqual(esperado);
  });

  it.each<Engine>(["webkit", "chromium"])(
    "no %s, uma navegação liberada que não desmonta a tela não desliga a guarda",
    (engine) => {
      const browser = new FakeBrowser(engine);
      let bloqueios = 0;
      browser.events.addEventListener(DIRTY_HISTORY_BLOCKED_EVENT, () => { bloqueios += 1; });
      const guard = createUnsavedHistoryGuard(browser.env, true);
      const avisos: boolean[] = [];
      browser.events.addEventListener("beforeunload", (event) => { avisos.push(event.defaultPrevented); });

      // "Descartar e ir" para o mesmo editor em outra URL: a tela fica montada.
      browser.events.dispatchEvent(new Event("appsell:dirty-navigation-allowed"));
      browser.routerPush("https://app.test/products?seller=s");
      guard.setDirty(false);
      browser.settle();
      const entradaDoDestino = browser.index;

      // Uma alteração nova volta a ser protegida: sentinela, diálogo e aviso.
      guard.setDirty(true);
      expect(browser.index).toBe(entradaDoDestino + 1);
      browser.events.dispatchEvent(new Event("beforeunload", { cancelable: true }));
      expect(avisos).toEqual([true]);
      browser.userBack();
      browser.settle();
      expect(bloqueios).toBe(1);
      expect(browser.leftProduct).toBe(false);
      expect(browser.index).toBe(entradaDoDestino + 1);
    }
  );

  it.each<Engine>(["webkit", "chromium"])(
    "no %s, sujar de novo com a navegação liberada ainda em voo não a desfaz",
    (engine) => {
      const browser = new FakeBrowser(engine);
      const guard = createUnsavedHistoryGuard(browser.env, true);
      const avisos: boolean[] = [];
      browser.events.addEventListener("beforeunload", (event) => { avisos.push(event.defaultPrevented); });

      // "Descartar e ir": a tela limpa e volta a sujar antes de o
      // `router.push` terminar.
      browser.events.dispatchEvent(new Event("appsell:dirty-navigation-allowed"));
      guard.setDirty(false);
      guard.setDirty(true);
      expect(browser.hasPending()).toBe(false);
      browser.routerPush("https://app.test/products?seller=s");
      const destino = browser.index;

      // O destino desmonta a tela: o vendedor fica onde a navegação o levou.
      guard.dispose();
      browser.settle();
      expect(browser.index).toBe(destino);
      expect(browser.leftProduct).toBe(false);
      // E a guarda desmontada não avisa ao sair da página.
      browser.events.dispatchEvent(new Event("beforeunload", { cancelable: true }));
      expect(avisos).toEqual([false]);
    }
  );

  it.each<Engine>(["webkit", "chromium"])(
    "no %s, um link de fragmento com a tela suja não empilha outra sentinela nem abre diálogo",
    (engine) => {
      const browser = new FakeBrowser(engine);
      let bloqueios = 0;
      browser.events.addEventListener(DIRTY_HISTORY_BLOCKED_EVENT, () => { bloqueios += 1; });
      const guard = createUnsavedHistoryGuard(browser.env, true);
      const sentinela = browser.index;

      // O skip-link empilha uma entrada do próprio vendedor em cima da
      // sentinela. Ela continua embaixo, protegendo: nada de sentinela nova
      // enterrando a antiga (#615, ponto E).
      browser.fragment("conteudo");
      browser.settle();
      browser.elapse();
      expect(browser.entries.length).toBe(sentinela + 2);
      expect(browser.url).toBe(`${PRODUCT_URL}#conteudo`);
      expect(bloqueios).toBe(0);

      // Voltar do fragmento cai na sentinela: é o lugar de antes, na mesma
      // página, sem diálogo.
      browser.userBack();
      browser.settle();
      browser.elapse();
      expect(browser.index).toBe(sentinela);
      expect(bloqueios).toBe(0);

      // Voltar de novo é sair com alteração pendente: diálogo.
      browser.userBack();
      browser.settle();
      expect(bloqueios).toBe(1);
      expect(browser.index).toBe(sentinela);
      expect(browser.leftProduct).toBe(false);

      // Salvar limpa a tela e a sentinela sai.
      guard.setDirty(false);
      browser.settle();
      expect(browser.index).toBe(sentinela - 1);
    }
  );

  it.each<Engine>(["webkit", "chromium"])(
    "no %s, salvar depois do fragmento não deixa a sentinela no caminho do voltar",
    (engine) => {
      const browser = new FakeBrowser(engine);
      let bloqueios = 0;
      browser.events.addEventListener(DIRTY_HISTORY_BLOCKED_EVENT, () => { bloqueios += 1; });
      const guard = createUnsavedHistoryGuard(browser.env, true);
      const sentinela = browser.index;
      browser.fragment("conteudo");
      browser.settle();
      guard.setDirty(false);
      browser.settle();
      expect(bloqueios).toBe(0);

      // Limpa, o voltar do fragmento atravessa a sentinela sozinho: a guarda
      // pousa nela e já segue para a entrada de antes.
      browser.userBack();
      browser.settle();
      expect(browser.index).toBe(sentinela - 1);
      expect(bloqueios).toBe(0);
      expect(browser.leftProduct).toBe(false);
    }
  );

  it.each<Engine>(["webkit", "chromium"])(
    "no %s, o go(1) que chega depois do prazo pousa numa sentinela que a guarda reconhece",
    (engine) => {
      const browser = new FakeBrowser(engine);
      let bloqueios = 0;
      browser.events.addEventListener(DIRTY_HISTORY_BLOCKED_EVENT, () => { bloqueios += 1; });
      const guard = createUnsavedHistoryGuard(browser.env, true);
      const sentinela = browser.index;

      // O voltar do usuário: a guarda pede `go(1)`, mas a travessia demora. O
      // vendedor salva nesse meio-tempo e o prazo esgota com a tela limpa.
      browser.userBack();
      guard.setDirty(false);
      browser.elapse();
      // A travessia chega atrasada e pousa na sentinela (#615, ponto B).
      browser.step();
      expect(browser.index).toBe(sentinela);

      // A guarda a reconhece pela marca e a tira: a tela limpa não fica em
      // cima de uma sentinela que ninguém mais conta.
      browser.settle();
      expect(browser.index).toBe(sentinela - 1);
      expect(bloqueios).toBe(0);
      expect(browser.leftProduct).toBe(false);
    }
  );

  it.each<Engine>(["webkit", "chromium"])(
    "no %s, uma navegação liberada para a mesma URL rearma a guarda sem empilhar entrada",
    (engine) => {
      const browser = new FakeBrowser(engine);
      let bloqueios = 0;
      browser.events.addEventListener(DIRTY_HISTORY_BLOCKED_EVENT, () => { bloqueios += 1; });
      const guard = createUnsavedHistoryGuard(browser.env, true);
      const sentinela = browser.index;

      // "Descartar e ir" para a própria URL: o Next troca o estado da entrada
      // em vez de empilhar, e a tela continua montada (#615, ponto F).
      browser.events.dispatchEvent(new Event("appsell:dirty-navigation-allowed"));
      guard.setDirty(false);
      browser.routerPush(PRODUCT_URL);

      guard.setDirty(true);
      expect(browser.entries.length).toBe(sentinela + 1);
      browser.userBack();
      browser.settle();
      expect(bloqueios).toBe(1);
      expect(browser.leftProduct).toBe(false);
    }
  );

  it.each<Engine>(["webkit", "chromium"])(
    "no %s, a URL trocada com a sentinela no topo sobrevive à saída dela",
    (engine) => {
      const browser = new FakeBrowser(engine);
      const guard = createUnsavedHistoryGuard(browser.env, true);
      browser.routerReplace(`${PRODUCT_URL}?section=oferta`);
      guard.setDirty(false);
      browser.settle();
      expect(browser.index).toBe(1);
      expect(browser.url).toBe(`${PRODUCT_URL}?section=oferta`);
      // Pelo router do app, não por `replaceState` por fora dele (#615, C).
      expect(browser.routerReplaces).toEqual([`${PRODUCT_URL}?section=oferta`]);
      expect(browser.entries[1].state).toEqual({ __NA: true });
    }
  );

  it.each<Engine>(["webkit", "chromium"])(
    "no %s, a troca de etapa pedida enquanto a sentinela sai espera o popstate dela",
    (engine) => {
      const browser = new FakeBrowser(engine);
      const guard = createUnsavedHistoryGuard(browser.env, true);
      const navegacoes: string[] = [];
      // "Descartar": a tela fica limpa e a etapa troca no mesmo evento. Se a
      // troca fosse antes do popstate do `back()`, o Next restauraria a etapa
      // de onde o vendedor saiu (#615, C).
      guard.setDirty(false);
      guard.whenSettled(() => {
        navegacoes.push(browser.url);
        browser.routerReplace(`${PRODUCT_URL}?section=oferta`);
      });
      expect(navegacoes).toEqual([]);
      browser.settle();
      expect(navegacoes).toEqual([PRODUCT_URL]);
      expect(browser.index).toBe(1);
      expect(browser.url).toBe(`${PRODUCT_URL}?section=oferta`);
      // Um único voltar sai da tela: não sobrou sentinela.
      browser.userBack();
      browser.settle();
      expect(browser.leftProduct).toBe(true);
    }
  );

  it.each<Engine>(["webkit", "chromium"])(
    "no %s, se o popstate da saída nunca chega, a troca de etapa segue no prazo",
    (engine) => {
      const browser = new FakeBrowser(engine);
      const guard = createUnsavedHistoryGuard(browser.env, true);
      let navegou = 0;
      guard.setDirty(false);
      guard.whenSettled(() => { navegou += 1; });
      // O `back()` da guarda foi pedido e o navegador não o entregou: sem o
      // prazo, o vendedor ficaria preso no editor.
      expect(navegou).toBe(0);
      browser.elapse();
      expect(navegou).toBe(1);
      // O popstate atrasado não repete a navegação que já seguiu.
      browser.settle();
      expect(navegou).toBe(1);
    }
  );

  it("a navegação que assentou a tempo não roda de novo no prazo", () => {
    const browser = new FakeBrowser("webkit");
    const guard = createUnsavedHistoryGuard(browser.env, true);
    let navegou = 0;
    guard.setDirty(false);
    guard.whenSettled(() => { navegou += 1; });
    browser.settle();
    expect(navegou).toBe(1);
    browser.elapse();
    expect(navegou).toBe(1);
  });

  it("com o histórico assentado, a navegação pedida roda na hora", () => {
    const browser = new FakeBrowser("webkit");
    const guard = createUnsavedHistoryGuard(browser.env, true);
    let navegou = 0;
    guard.whenSettled(() => { navegou += 1; });
    expect(navegou).toBe(1);
    guard.setDirty(false);
    browser.settle();
    guard.whenSettled(() => { navegou += 1; });
    expect(navegou).toBe(2);
  });

  it("desmontar com navegação à espera deixa ela seguir", () => {
    const browser = new FakeBrowser("chromium");
    const guard = createUnsavedHistoryGuard(browser.env, true);
    let navegou = 0;
    guard.setDirty(false);
    guard.whenSettled(() => { navegou += 1; });
    guard.dispose();
    expect(navegou).toBe(1);
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
