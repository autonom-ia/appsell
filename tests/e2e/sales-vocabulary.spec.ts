import { expect, test, type Page } from "@playwright/test";

import { BANNED_SALES_TERMS, findBannedTerms } from "../../lib/sales-vocabulary";

/**
 * A4.5 — os termos de sistema não aparecem na área de vendas, e o CI falha se
 * voltarem.
 *
 * A varredura é sobre o texto **renderizado**, não sobre o código-fonte. Fosse
 * sobre o fonte, ela acusaria `payload.snapshot` — nome de campo da API, que
 * ninguém lê na tela — e a correção seria renomear o contrato para calar um
 * teste. Lendo o que o navegador mostra, só sobra o que o vendedor realmente vê.
 */

const SELLER_ID = "4df98a2e-d1dd-5f25-bbea-58b8072a0e7b";
const PRODUCT_ID = "82efefc5-a3c9-5126-a7d1-4918d5428d63";

async function login(page: Page) {
  await page.goto("/");
  await page.getByRole("link", { name: "Entrar no ambiente local" }).click();
  await expect(page).toHaveURL("/dashboard");
}

const EDITOR_SECTIONS = ["produto", "curriculo", "oferta", "publicar"] as const;

const COURSE_ID = "9a4a73d6-bf11-5429-8582-89434000af44";

/**
 * O identificador da área continua `curriculo` e `oferta` na URL: endereço já
 * em circulação é contrato, e trocá-lo quebraria links salvos. O portão lê o
 * texto renderizado, não a barra de endereço.
 */
async function mockEditor(page: Page, options: { extraCourse?: boolean } = {}) {
  await page.route(`**/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}`, async (route) => {
    await route.fulfill({
      json: {
        id: PRODUCT_ID,
        seller_organization_id: SELLER_ID,
        seller_slug: "autonomia",
        slug: "ia-na-pratica",
        title: "IA na prática",
        description: "Conteúdo de teste",
        status: "draft",
        draft_version: 3,
        course: { id: COURSE_ID, title: "Especialista", description: "", status: "draft", draft_version: 2, position: 1 },
        courses: options.extraCourse
          ? [{ id: COURSE_ID, title: "Especialista" }, { id: "segundo", title: "Avançado" }]
          : [],
        offer: { id: "b1d0c9f6-5a1e-52a4-9c3f-2f0f1d3a7e55", title: "Padrão", summary: "", status: "draft", draft_version: 1, unit_amount_minor: 4990, currency: "BRL", billing_period: "one_time" },
        curriculum: { modules: 1, lessons: 2 },
        publication: null,
        has_unpublished_changes: true,
        readiness: { can_publish: true, has_unpublished_changes: true, items: [{ key: "k", label: "Validação do servidor concluída.", ready: true }] },
        csrf: { update: "u", publish: "p", offer_status: "o" }
      }
    });
  });
  await page.route("**/api/sellers/**/curriculum", async (route) => {
    await route.fulfill({
      json: {
        course: { id: COURSE_ID, title: "Especialista", draft_version: 2 },
        modules: [{ id: "mod-1", title: "Fundamentos", position: 1, lessons: [] }],
        limits: { modules_per_course: 20, lessons_per_module: 30, lessons_per_course: 200 },
        usage: { modules_total: 1, lessons_total: 0, lessons_by_module: {} },
        csrf: { create: "c", update: "u", reorder: "r", archive: "a" }
      }
    });
  });
  await page.route("**/materials", async (route) => {
    await route.fulfill({ json: { materials: [], csrf: { create: "c", update: "u", reorder: "r", archive: "a" } } });
  });
  await page.route("**/access-links", async (route) => {
    await route.fulfill({ json: { access_links: [] } });
  });
}

async function visibleText(page: Page) {
  return await page.evaluate(() => document.body.innerText || "");
}

function report(hits: ReturnType<typeof findBannedTerms>, where: string) {
  return hits
    .map((hit) => `${where}: "${hit.term}" (diga "${hit.instead}") — ${hit.excerpt}`)
    .join("\n");
}

test("nenhum termo de sistema aparece nas telas de venda", async ({ page }) => {
  await login(page);
  await page.route(`**/api/sellers/${SELLER_ID}/products*`, async (route) => {
    await route.fulfill({
      json: {
        products: [{
          id: PRODUCT_ID,
          slug: "ia-na-pratica",
          title: "IA na prática",
          description: "Curso de teste",
          status: "published",
          created_at: "2026-08-25T00:00:00Z",
          course: { id: "c", title: "Especialista", status: "draft" },
          access_mode: "paid",
          unit_amount_minor: 4990,
          currency: "BRL"
        }],
        next_cursor: null
      }
    });
  });

  await mockEditor(page);

  // O editor é onde o vendedor passa mais tempo, e era justamente ele que
  // ficava de fora da varredura. As quatro áreas entram uma a uma: elas
  // renderizam telas diferentes, e olhar só a primeira deixaria as outras três
  // livres para dizer o que quisessem.
  const paths = [
    "/dashboard",
    "/products",
    "/payouts",
    ...EDITOR_SECTIONS.map((section) => `/products/${SELLER_ID}/${PRODUCT_ID}?section=${section}`)
  ];

  for (const path of paths) {
    await page.goto(path);
    // A tela precisa ter carregado alguma coisa, senão o teste passa por vazio.
    await expect(page.locator("h1, h2").first()).toBeVisible();
    const text = await visibleText(page);
    expect(text.length).toBeGreaterThan(80);

    const hits = findBannedTerms(text);
    expect(report(hits, path)).toBe("");
  }
});

test("a varredura acusa um termo banido quando ele existe de fato", async ({ page }) => {
  // Sem esta prova o teste acima poderia estar sempre verde por não achar nada
  // em lugar nenhum. Aqui um termo é injetado de propósito e precisa ser pego.
  await login(page);
  await page.goto("/dashboard");
  await page.evaluate(() => {
    const injected = document.createElement("p");
    injected.textContent = "Este snapshot versionado não deveria estar visível.";
    document.body.appendChild(injected);
  });

  const hits = findBannedTerms(await visibleText(page));

  expect(hits.map((hit) => hit.term)).toContain("snapshot");
});

test("a lista de termos banidos não está vazia", async () => {
  // Um portão sem termos é um portão aberto que parece fechado.
  expect(BANNED_SALES_TERMS.length).toBeGreaterThan(0);
});

test("com um só curso, a identidade do produto não fala em curso", async ({ page }) => {
  // B3.4 — decisão travada do Pacote A, nunca cumprida até aqui: o cabeçalho
  // dizia "@ia-na-pratica · curso Especialista" e o olho subia até
  // "Espaço selecionado · 4df98a2e…". Nenhum dos dois é informação de quem
  // vende um produto só.
  await login(page);
  await mockEditor(page);
  await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=produto`);

  const identity = page.locator(".editor-title-row");
  await expect(identity).toBeVisible();
  const text = (await identity.innerText()).toLowerCase();

  expect(text).not.toContain("curso");
  expect(text).not.toContain("espaço");
  // E nada de identificador: os oito primeiros caracteres do UUID da loja
  // apareciam ali como se fossem o nome dela.
  expect(text).not.toContain(SELLER_ID.slice(0, 8));
  // O que sobra é o que serve: o endereço da loja e o do produto.
  expect(text).toContain("@autonomia");
  expect(text).toContain("@ia-na-pratica");
});

test("com dois cursos, o nome do curso volta — ele passa a ser informação", async ({ page }) => {
  // Esconder é uma decisão sobre o caso de um curso só, não sobre a palavra.
  // Sem esta prova, apagar a condicional deixaria o teste acima verde.
  await login(page);
  await mockEditor(page, { extraCourse: true });
  await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=produto`);

  const identity = page.locator(".editor-title-row");
  await expect(identity).toBeVisible();
  expect(await identity.innerText()).toContain("Especialista");
});
