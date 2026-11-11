import { expect as expectBase, test, type Page } from "@playwright/test";
import { login } from "./support/login";

// A guarda de "alterações não salvas" protege o voltar com uma entrada extra
// no histórico. Ela entra quando a tela fica suja e sai quando fica limpa.
// Quando o estado oscila rápido — num upload de anexo ele vai e volta duas
// vezes em menos de 100 ms —, a guarda empilhava `pushState` e
// `history.back()` com uma travessia ainda em voo e lia o `popstate` da
// própria travessia como gesto do usuário. No WebKit uma navegação de
// histórico agendada cancela a anterior, e a conta fechava com um passo a
// menos: o vendedor saía da tela do produto sem ter pedido (#612).
const expect = expectBase.configure({ timeout: 20_000 });

const SELLER_ID = "4df98a2e-d1dd-5f25-bbea-58b8072a0e7b";
const PRODUCT_ID = "82efefc5-a3c9-5126-a7d1-4918d5428d63";
const PRODUCT_PATH = `/products/${SELLER_ID}/${PRODUCT_ID}`;
const TITULO = "IA na prática";
const OSCILACOES = 12;

async function openEditor(page: Page) {
  await page.route(`**/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}`, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({ json: {
      id: PRODUCT_ID, seller_organization_id: SELLER_ID, seller_slug: "autonomia",
      slug: "ia-na-pratica", title: TITULO, description: "Conteúdo",
      status: "draft", draft_version: 3,
      course: { id: "9a4a73d6-bf11-5429-8582-89434000af44", title: "Especialista", description: "", status: "draft", draft_version: 2, position: 1 },
      courses: [],
      offer: { id: "b1d0c9f6-5a1e-52a4-9c3f-2f0f1d3a7e55", title: "Padrão", summary: "", status: "draft", draft_version: 1, unit_amount_minor: 0, currency: "BRL", billing_period: "one_time" }
    } });
  });
  await page.goto(PRODUCT_PATH);
  await expect(page.getByLabel("Nome do produto")).toHaveValue(TITULO);
}

// Toda navegação do quadro principal que saia da tela do produto. O
// `toHaveURL` sozinho não basta: a saída indevida chega depois de a
// oscilação terminar, e uma asserção de URL feita cedo passaria.
function saidasDoProduto(page: Page) {
  const saidas: string[] = [];
  page.on("framenavigated", (frame) => {
    if (frame !== page.mainFrame()) return;
    if (!new URL(frame.url()).pathname.startsWith(PRODUCT_PATH)) saidas.push(new URL(frame.url()).pathname);
  });
  return saidas;
}

declare global {
  interface Window { __e2eDefinirNome?: (valor: string) => void }
}

// Muda o nome do produto como o React enxerga uma digitação: valor nativo e
// evento `input`. Evento `input` é discreto para o React, que renderiza e roda
// os efeitos dentro do próprio despacho — a guarda reage antes de a função
// voltar, sem esperar o navegador andar. Fica instalado na janela porque a
// CSP do build não deixa montar função a partir de texto.
async function instalarDefinirNome(page: Page) {
  await page.evaluate(() => {
    window.__e2eDefinirNome = (valor: string) => {
      const campo = document.querySelector<HTMLInputElement>('input[name="product_title"]');
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      if (!campo || !setter) throw new Error("campo Nome do produto ausente");
      setter.call(campo, valor);
      campo.dispatchEvent(new Event("input", { bubbles: true }));
    };
  });
}

test("a tela do produto fica onde está quando o estado oscila rápido", async ({ page }) => {
  await login(page);
  await openEditor(page);
  const saidas = saidasDoProduto(page);
  const nome = page.getByLabel("Nome do produto");
  await instalarDefinirNome(page);

  // A ordem que tirava o vendedor da tela no WebKit, a mesma do upload de
  // anexo: suja, limpa e suja de novo sem o navegador andar; deixa uma única
  // travessia acontecer; e limpa antes da seguinte. Com a guarda antiga, essa
  // travessia era o `back()` da limpeza, a guarda nova a tomava por gesto do
  // usuário e pedia `go(1)`, e o `back()` da última limpeza cancelava o
  // `go(1)` e saía da página. O listener entra depois da guarda, então roda
  // depois dela no mesmo `popstate`.
  await page.evaluate(async (titulo) => {
    const definirNome = window.__e2eDefinirNome!;
    definirNome(`${titulo}x`);
    definirNome(titulo);
    definirNome(`${titulo}x`);
    // Sem travessia não há o que testar: se o React deixar de rodar os efeitos
    // dentro do despacho do `input`, a guarda não pede `back()` aqui e o
    // teste falha com o motivo escrito, em vez de esperar o tempo todo.
    await new Promise<void>((resolve, reject) => {
      const prazo = window.setTimeout(() => {
        reject(new Error("a guarda não pediu nenhuma travessia depois de limpar a tela"));
      }, 5_000);
      window.addEventListener("popstate", () => {
        window.clearTimeout(prazo);
        definirNome(titulo);
        resolve();
      }, { once: true });
    });
  }, TITULO);
  await expect(nome).toHaveValue(TITULO);

  // Oscilações seguidas num único despacho também não podem sair da tela.
  await page.evaluate(({ titulo, vezes }) => {
    const definirNome = window.__e2eDefinirNome!;
    for (let i = 0; i < vezes; i += 1) {
      definirNome(`${titulo}x`);
      definirNome(titulo);
    }
  }, { titulo: TITULO, vezes: OSCILACOES });
  await expect(nome).toHaveValue(TITULO);

  // A tela termina suja e o voltar tem de abrir o diálogo, não sair. Se a
  // conta do histórico tivesse perdido um passo, a página já estaria fora
  // daqui e o campo nem existiria.
  await nome.focus();
  await page.keyboard.press("End");
  await page.keyboard.type("!");
  await expect(nome).toHaveValue(`${TITULO}!`);
  await page.goBack();
  await expect(page.getByRole("dialog", { name: "Sair desta edição?" })).toBeVisible();
  await page.getByRole("button", { name: "Continuar editando" }).click();
  await expect(page).toHaveURL(PRODUCT_PATH);
  await expect(nome).toHaveValue(`${TITULO}!`);
  expect(saidas).toEqual([]);

  // Limpa de novo, um único voltar leva para a página anterior: a guarda não
  // deixou entrada sobrando no histórico.
  await nome.focus();
  await page.keyboard.press("End");
  await page.keyboard.press("Backspace");
  await expect(nome).toHaveValue(TITULO);
  await expect(page.getByText("Há alterações não salvas.")).toHaveCount(0);
  await page.goBack();
  await expect(page).toHaveURL("/dashboard");
});

// Troca de etapa com alteração pendente (#615, ponto C). As etapas usam
// `router.replace`: a URL muda com a sentinela da guarda ainda no topo, e a
// guarda a tira logo depois. O que se prova é o que o vendedor vê — a URL e a
// etapa na tela concordam, agora e depois de voltar e avançar.
test.describe("troca de etapa com alteração pendente", () => {
  const COURSE_ID = "9a4a73d6-bf11-5429-8582-89434000af44";

  function produto(titulo: string) {
    return {
      id: PRODUCT_ID, seller_organization_id: SELLER_ID, seller_slug: "autonomia",
      slug: "ia-na-pratica", title: titulo, description: "Conteúdo",
      status: "draft", draft_version: 3,
      course: { id: COURSE_ID, title: "Especialista", description: "", status: "draft", draft_version: 2, position: 1 },
      courses: [],
      offer: { id: "b1d0c9f6-5a1e-52a4-9c3f-2f0f1d3a7e55", title: "Padrão", summary: "", status: "draft", draft_version: 1, unit_amount_minor: 0, currency: "BRL", billing_period: "one_time" },
      curriculum: { modules: 0, lessons: 0 },
      publication: null,
      has_unpublished_changes: false,
      readiness: { can_publish: false, has_unpublished_changes: false, items: [] },
      csrf: { update: "u", publish: "p", offer_status: "o" }
    };
  }

  async function abrirNaEtapaProduto(page: Page) {
    let titulo = TITULO;
    await page.route(`**/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}`, async (route) => {
      if (route.request().method() === "PATCH") {
        const posted = route.request().postDataBuffer();
        const form = await new Request(route.request().url(), {
          method: "PATCH", headers: route.request().headers(), body: posted ? new Uint8Array(posted) : undefined
        }).formData();
        titulo = String(form.get("product_title"));
        return route.fulfill({ json: { ok: true } });
      }
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({ json: produto(titulo) });
    });
    await page.goto(`${PRODUCT_PATH}?section=produto`);
    await expect(page.getByLabel("Nome do produto")).toHaveValue(TITULO);
  }

  // A guarda terminou o que tinha a fazer: nenhuma sentinela dela no topo.
  async function guardaAssentada(page: Page) {
    await expect.poll(() => page.evaluate(() => {
      const state = history.state as { __appsellDirtyGuard?: unknown } | null;
      return state?.__appsellDirtyGuard ?? null;
    })).toBeNull();
  }

  async function confereEtapaPreco(page: Page) {
    await expect(page).toHaveURL(`${PRODUCT_PATH}?section=oferta`);
    await expect(page.getByLabel("Preço (R$)")).toBeVisible();
    await expect(page.getByLabel("Nome do produto")).toHaveCount(0);
  }

  for (const saida of ["Descartar", "Salvar e continuar"] as const) {
    test(`"${saida}" leva à etapa pedida, e voltar e avançar mantêm URL e tela juntas`, async ({ page }) => {
      await login(page);
      await abrirNaEtapaProduto(page);
      await page.getByLabel("Nome do produto").fill(`${TITULO} 2`);
      await page.getByRole("navigation", { name: "Áreas do produto" })
        .getByRole("link", { name: /^Preço/ }).click();
      await expect(page.getByRole("heading", { name: "Guardar antes de sair desta etapa?" })).toBeVisible();
      await page.getByRole("button", { name: saida }).click();

      await confereEtapaPreco(page);
      await guardaAssentada(page);
      await confereEtapaPreco(page);

      // As etapas trocam a URL sem empilhar: um voltar sai do editor.
      await page.goBack();
      await expect(page).toHaveURL("/dashboard");
      // E avançar volta à etapa em que o vendedor estava, com a tela dela.
      await page.goForward();
      await confereEtapaPreco(page);
    });
  }
});
