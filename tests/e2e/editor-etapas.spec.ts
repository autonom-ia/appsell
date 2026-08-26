import { expect, test, type Page } from "@playwright/test";

/**
 * #258 — cada etapa do editor diz o próprio estado, e nada da Onda 3 regride.
 *
 * A metade de baixo deste arquivo existe por causa do aviso do issue: a Onda 3
 * (#231) entregou navegação livre, autosave e prontidão do servidor, e mexer na
 * navegação é justamente o tipo de mudança que derruba isso sem avisar. Os
 * critérios A3.x têm testes próprios; estes aqui provam que a etapa renomeada
 * não quebrou o endereço antigo nem o percurso por teclado.
 */

const SELLER_ID = "4df98a2e-d1dd-5f25-bbea-58b8072a0e7b";
const PRODUCT_ID = "82efefc5-a3c9-5126-a7d1-4918d5428d63";
const COURSE_ID = "9a4a73d6-bf11-5429-8582-89434000af44";

interface Shape {
  modules: number;
  lessons: number;
  offerStatus: string;
  amount: number;
  published: boolean;
  unpublishedChanges: boolean;
}

function payload(shape: Partial<Shape> = {}) {
  const it: Shape = {
    modules: 2, lessons: 5, offerStatus: "draft", amount: 0,
    published: false, unpublishedChanges: false, ...shape
  };
  return {
    id: PRODUCT_ID,
    seller_organization_id: SELLER_ID,
    seller_slug: "autonomia",
    slug: "ia-na-pratica",
    title: "IA na prática",
    description: "Etapas",
    status: it.published ? "published" : "draft",
    draft_version: 3,
    course: { id: COURSE_ID, title: "Especialista", description: "", status: "draft", draft_version: 2, position: 1 },
    courses: [],
    offer: {
      id: "b1d0c9f6-5a1e-52a4-9c3f-2f0f1d3a7e55", title: "Padrão", summary: "",
      status: it.offerStatus, draft_version: 1, unit_amount_minor: it.amount,
      currency: "BRL", billing_period: "one_time"
    },
    curriculum: { modules: it.modules, lessons: it.lessons },
    publication: it.published ? { id: "pub", version: 1, published_at: "2026-08-25T12:00:00Z" } : null,
    has_unpublished_changes: it.unpublishedChanges,
    readiness: { can_publish: true, has_unpublished_changes: it.unpublishedChanges, items: [] },
    csrf: { update: "u", publish: "p", offer_status: "o" }
  };
}

async function login(page: Page) {
  await page.goto("/");
  await page.getByRole("link", { name: "Entrar no ambiente local" }).click();
  await expect(page).toHaveURL("/dashboard");
}

async function openEditor(page: Page, shape: Partial<Shape> = {}) {
  await page.route(`**/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}`, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({ json: payload(shape) });
  });
  await page.route("**/api/sellers/**/curriculum", async (route) => {
    await route.fulfill({ json: { course: { id: COURSE_ID, title: "Especialista" }, modules: [], limits: { modules_per_course: 20, lessons_per_module: 30, lessons_per_course: 200 }, usage: { modules_total: 0, lessons_total: 0, lessons_by_module: {} }, csrf: {} } });
  });
  await page.route("**/materials", async (route) => {
    await route.fulfill({ json: { materials: [], csrf: {} } });
  });
  await page.route("**/access-links", async (route) => {
    await route.fulfill({ json: { access_links: [] } });
  });
  await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=produto`);
  await expect(page.getByRole("heading", { name: "IA na prática" })).toBeVisible();
}

function nav(page: Page) {
  return page.getByRole("navigation", { name: "Áreas do produto" });
}

test.describe("#258 — o estado de cada etapa, escrito", () => {
  test("B6.1 — a etapa conta o que tem, em vez de mostrar uma bolinha", async ({ page }) => {
    await login(page);
    await openEditor(page, { modules: 2, lessons: 5 });

    // A dica era fixa: "Módulos e aulas", igual num curso vazio e num curso
    // cheio. A bolinha ao lado não dizia se estava pronto nem o que faltava.
    await expect(nav(page).getByRole("link", { name: /^Conteúdo/ })).toContainText("2 módulos, 5 aulas");
    await expect(nav(page).getByRole("link", { name: /^Preço/ })).toContainText("Gratuito");
    await expect(nav(page).getByRole("link", { name: /^Distribuição/ })).toContainText("Ainda não está no ar");
  });

  test("B6.1 — uma etapa vazia nomeia a falta", async ({ page }) => {
    await login(page);
    await openEditor(page, { modules: 0, lessons: 0 });

    await expect(nav(page).getByRole("link", { name: /^Conteúdo/ })).toContainText("Nenhum módulo ainda");
  });

  test("B6.1 — no ar, a etapa diz desde quando", async ({ page }) => {
    await login(page);
    await openEditor(page, { published: true });

    await expect(nav(page).getByRole("link", { name: /^Distribuição/ })).toContainText("No ar desde 25 de agosto");
  });

  test("B6.1 — com mudança pendente, a pendência ganha da data", async ({ page }) => {
    await login(page);
    await openEditor(page, { published: true, unpublishedChanges: true });

    // Saber que existe algo por publicar importa mais do que saber quando foi a
    // última publicação.
    await expect(nav(page).getByRole("link", { name: /^Distribuição/ })).toContainText("mudanças por publicar");
  });

  test("B6.2 — nenhum identificador técnico no cabeçalho", async ({ page }) => {
    await login(page);
    await openEditor(page, { published: true });

    const header = await page.locator(".editor-title-row").innerText();
    // Os oito primeiros caracteres do UUID da loja apareciam ali como se
    // fossem o nome dela.
    expect(header).not.toContain(SELLER_ID.slice(0, 8));
    expect(header.toLowerCase()).not.toContain("espaço");
    // E o que sobra é o que serve.
    expect(header).toContain("@ia-na-pratica");
    expect(header).toContain("no ar desde 25 de agosto");
  });

  test("B6.3 — o endereço antigo continua abrindo (A3.6)", async ({ page }) => {
    await login(page);
    await openEditor(page);

    // `?section=publicar` circula em links salvos; a área mudou de nome, não de
    // identificador. Trocar o identificador quebraria endereço já em uso.
    await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=publicar`);
    await expect(page.getByRole("heading", { name: "Publicar catálogo" })).toBeVisible();
    await expect(nav(page).getByRole("link", { name: /^Distribuição/ })).toHaveAttribute("aria-current", "page");

    // E uma área que nunca existiu continua caindo em Produto, sem erro.
    await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=preview`);
    await expect(page.getByRole("heading", { name: "IA na prática" })).toBeVisible();
  });

  test("B6.5 — a etapa renomeada continua alcançável por teclado (A3.5)", async ({ page, isMobile }) => {
    test.skip(isMobile === true, "percurso por teclado não se aplica ao perfil móvel");
    await login(page);
    await openEditor(page);

    await page.locator("body").click({ position: { x: 1, y: 1 } });
    let label = "";
    for (let step = 0; step < 40 && !/Distribuição/.test(label); step += 1) {
      await page.keyboard.press("Tab");
      label = await page.evaluate(() => (document.activeElement?.textContent ?? "").trim());
    }
    expect(label).toContain("Distribuição");

    // O anel de foco precisa estar desenhado de fato: `:focus-visible` casa
    // depois de um foco programático mesmo sem anel nenhum, então o que conta é
    // a largura computada do contorno.
    const ring = await page.evaluate(() => {
      const style = getComputedStyle(document.activeElement as HTMLElement);
      return style.outlineStyle !== "none" && parseFloat(style.outlineWidth || "0") > 0;
    });
    expect(ring).toBe(true);

    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/section=publicar/);
  });

  test("as quatro etapas seguem abertas em qualquer ordem (A3.2)", async ({ page }) => {
    await login(page);
    await openEditor(page);

    // Resumo de estado não é portão: nenhuma etapa bloqueia outra.
    await expect(nav(page).getByRole("link")).toHaveCount(4);
    const journey = [
      { name: /^Distribuição/, url: /section=publicar/ },
      { name: /^Preço/, url: /section=oferta/ },
      { name: /^Conteúdo/, url: /section=curriculo/ }
    ];
    for (const stop of journey) {
      await nav(page).getByRole("link", { name: stop.name }).click();
      await expect(page).toHaveURL(stop.url);
    }
  });
});
