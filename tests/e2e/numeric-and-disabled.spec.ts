import { expect, test, type Page } from "@playwright/test";

/**
 * #253 e #254 — um campo numérico não aceita o que o servidor recusa, e nenhum
 * botão fica desligado sem dizer o que falta.
 *
 * Estes dois critérios não se conferem lendo o código. O primeiro depende do
 * que o navegador deixa entrar no campo; o segundo depende do que a folha de
 * estilo realmente pinta — e foi exatamente aí que a interface mentiu: o botão
 * "Criar link" tinha `disabled` no DOM e `opacity: 1` com `cursor: pointer` na
 * tela, indistinguível de um botão vivo que ignora o clique.
 *
 * Por isso as asserções abaixo leem `getComputedStyle`, não atributos. Apagar a
 * regra `.secondary-button:disabled` do CSS faz este arquivo falhar.
 */

const SELLER_ID = "4df98a2e-d1dd-5f25-bbea-58b8072a0e7b";
const PRODUCT_ID = "82efefc5-a3c9-5126-a7d1-4918d5428d63";
const COURSE_ID = "9a4a73d6-bf11-5429-8582-89434000af44";
const OFFER_ID = "b1d0c9f6-5a1e-52a4-9c3f-2f0f1d3a7e55";

function productPayload() {
  return {
    id: PRODUCT_ID,
    seller_organization_id: SELLER_ID,
    seller_slug: "autonomia",
    slug: "ia-na-pratica",
    title: "IA na prática",
    description: "Curso de campos numéricos",
    status: "draft",
    draft_version: 3,
    course: {
      id: COURSE_ID,
      title: "Especialista",
      description: "",
      status: "draft",
      draft_version: 2,
      position: 1
    },
    courses: [],
    offer: {
      id: OFFER_ID,
      title: "Oferta padrão",
      summary: "",
      status: "draft",
      draft_version: 1,
      unit_amount_minor: 4990,
      currency: "BRL",
      billing_period: "one_time"
    },
    curriculum: { modules: 1, lessons: 2 },
    publication: null,
    has_unpublished_changes: true,
    readiness: {
      can_publish: true,
      has_unpublished_changes: true,
      items: [{ key: "server_validation_complete", label: "Validação do servidor concluída.", ready: true }]
    },
    csrf: { update: "csrf-update", publish: "csrf-publish", offer_status: "csrf-offer" }
  };
}

async function login(page: Page) {
  await page.goto("/");
  await page.getByRole("link", { name: "Entrar no ambiente local" }).click();
  await expect(page).toHaveURL("/dashboard");
}

async function mockEditor(page: Page) {
  await page.route(`**/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}`, async (route) => {
    await route.fulfill({ json: productPayload() });
  });
  await page.route("**/api/sellers/**/curriculum", async (route) => {
    await route.fulfill({ json: { course: { id: COURSE_ID, title: "Especialista" }, modules: [], limits: { modules_per_course: 20, lessons_per_module: 30, lessons_per_course: 200 }, usage: { modules: 0, lessons_total: 0, lessons_by_module: {} }, csrf: {} } });
  });
  await page.route("**/materials", async (route) => {
    await route.fulfill({ json: { materials: [], csrf: { create: "c", update: "u", reorder: "r", archive: "a" } } });
  });
  await page.route("**/access-links", async (route) => {
    await route.fulfill({ json: { access_links: [] } });
  });
}

/**
 * Dois módulos, para que o primeiro tenha "Subir" desligado e o segundo não —
 * o par que prova a regra de estilo dos dois lados.
 */
async function mockEditorWithModules(page: Page) {
  await page.route(`**/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}`, async (route) => {
    await route.fulfill({ json: productPayload() });
  });
  await page.route("**/materials", async (route) => {
    await route.fulfill({ json: { materials: [], csrf: { create: "c", update: "u", reorder: "r", archive: "a" } } });
  });
  await page.route("**/api/sellers/**/curriculum", async (route) => {
    await route.fulfill({
      json: {
        course: { id: COURSE_ID, title: "Especialista", draft_version: 2 },
        modules: [
          { id: "mod-1", title: "Fundamentos", position: 1, lessons: [] },
          { id: "mod-2", title: "Aprofundamento", position: 2, lessons: [] }
        ],
        limits: { modules_per_course: 20, lessons_per_module: 30, lessons_per_course: 200 },
        usage: { modules_total: 2, lessons_total: 0, lessons_by_module: {} },
        csrf: { create: "c", update: "u", reorder: "r", archive: "a" }
      }
    });
  });
}

/** O que a folha de estilo desenha de fato, não o que o atributo promete. */
async function paintedState(page: Page, selector: string) {
  return await page.evaluate((target) => {
    const element = document.querySelector(target);
    if (!element) return null;
    const style = getComputedStyle(element);
    return { opacity: Number(style.opacity), cursor: style.cursor };
  }, selector);
}

test.describe("#253 — o campo numérico recusa o impossível", () => {
  test("Vagas declara a faixa e não deixa escrever 'dez'", async ({ page }) => {
    await login(page);
    await mockEditor(page);
    await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=publicar`);

    const seats = page.getByLabel("Vagas");
    await expect(seats).toBeVisible();

    // A faixa é declarada ao navegador e ao leitor de tela, não só conferida
    // depois do envio.
    await expect(seats).toHaveAttribute("type", "number");
    await expect(seats).toHaveAttribute("min", "1");
    await expect(seats).toHaveAttribute("max", "100000");
    await expect(seats).toHaveAttribute("step", "1");

    // O limite fica visível antes de errar.
    await expect(page.getByText("De 1 a 100.000 vagas")).toBeVisible();

    // Este é o defeito relatado: o campo aceitava "dez" calado. Vale registrar
    // que `type="number"` sozinho já barra letra no Chromium — esta asserção
    // guarda o tipo do campo, não o filtro.
    await seats.pressSequentially("dez");
    await expect(seats).toHaveValue("");
    await expect(seats).toHaveAttribute("aria-invalid", "false");

    // Já esta guarda o filtro, e só ele: o campo não deixa passar do número de
    // dígitos do teto. Sem `keepDigits`, sobrariam sete e o valor viraria um
    // número que a API recusa.
    await seats.pressSequentially("9999999");
    await expect(seats).toHaveValue("999999");
  });

  test("Vagas acima do teto diz qual é o teto", async ({ page }) => {
    await login(page);
    await mockEditor(page);
    await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=publicar`);

    const seats = page.getByLabel("Vagas");
    await seats.pressSequentially("200000");
    await expect(seats).toHaveAttribute("aria-invalid", "true");
    // "Revise o campo destacado" era a resposta antiga, e não dizia o limite.
    await expect(page.getByRole("alert").filter({ hasText: "100.000" })).toBeVisible();
  });

  test("Preço recusa letra em vez de aceitar e reclamar no envio", async ({ page }) => {
    await login(page);
    await mockEditor(page);
    await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=oferta`);

    const price = page.locator("#offer_price");
    await expect(price).toBeVisible();
    await expect(price).toHaveAttribute("inputmode", "decimal");

    await price.fill("");
    await price.pressSequentially("dez");
    await expect(price).toHaveValue("");

    // A vírgula é preservada: um campo de dinheiro não reescreve o que a pessoa
    // digitou.
    await price.pressSequentially("49,90");
    await expect(price).toHaveValue("49,90");
  });

  test("Preço não acusa erro no meio de uma digitação válida", async ({ page }) => {
    await login(page);
    await mockEditor(page);
    await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=oferta`);

    const price = page.locator("#offer_price");
    await price.fill("");
    // "49," é um estado de passagem a caminho de "49,90". Julgado a cada tecla,
    // o campo ficaria vermelho acusando um erro que ainda está sendo digitado.
    await price.pressSequentially("49,");
    await expect(price).toHaveAttribute("aria-invalid", "false");

    await price.pressSequentially("90");
    await price.blur();
    await expect(price).toHaveAttribute("aria-invalid", "false");
  });

  test("Preço acusa, aí sim, quando a pessoa sai do campo com valor pela metade", async ({ page }) => {
    await login(page);
    await mockEditor(page);
    await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=oferta`);

    const price = page.locator("#offer_price");
    await price.fill("");
    await price.pressSequentially("49,");
    await price.blur();
    await expect(price).toHaveAttribute("aria-invalid", "true");

    // E some assim que for corrigido: depois do primeiro erro, o campo volta a
    // acompanhar a digitação em vez de esperar outro blur. `fill` e não
    // `pressSequentially`: reentrando no campo, o cursor volta ao início, e as
    // teclas cairiam antes da vírgula em vez de depois dela.
    await price.fill("49,90");
    await expect(price).toHaveAttribute("aria-invalid", "false");
  });
});

test.describe("#254 — nenhum botão desligado em silêncio", () => {
  test("'Criar link' não é desligado por campo vazio: ele diz o que falta", async ({ page }) => {
    await login(page);
    await mockEditor(page);
    await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=publicar`);

    const create = page.getByRole("button", { name: "Criar link" });
    await expect(create).toBeVisible();
    // Nem `disabled` nem `aria-disabled`: os dois anunciam "indisponível" e
    // tiram do vendedor a única forma de descobrir o que falta.
    await expect(create).toBeEnabled();

    await create.click();
    await expect(page.getByText("Dê um nome ao link para conseguir criá-lo.")).toBeVisible();
    await expect(page.getByLabel("Nome do link")).toBeFocused();
  });

  test("com vagas fora da faixa, acionar leva o foco ao campo errado", async ({ page }) => {
    await login(page);
    await mockEditor(page);
    await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=publicar`);

    await page.getByLabel("Nome do link").fill("Turma de agosto");
    await page.getByLabel("Vagas").pressSequentially("200000");

    await page.getByRole("button", { name: "Criar link" }).click();
    await expect(page.getByLabel("Vagas")).toBeFocused();
    await expect(page.getByRole("alert").filter({ hasText: "100.000" })).toBeVisible();
  });

  test("o botão que fica mesmo desligado parece desligado", async ({ page }) => {
    // O primeiro módulo nunca sobe. Este botão é `secondary-button` e fica
    // `disabled` de verdade — e era exatamente esta a classe que a folha de
    // estilo não tratava: opacity 1 e cursor pointer, idêntico a um botão vivo.
    // Apagar `.secondary-button:disabled` do CSS faz este teste falhar.
    await login(page);
    await mockEditorWithModules(page);
    await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=curriculo`);

    const moveUp = page.getByRole("button", { name: "Mover módulo Fundamentos para cima" });
    await expect(moveUp).toBeVisible();
    await expect(moveUp).toBeDisabled();

    // `expect.poll` e não uma leitura única: a folha anima `opacity`, e uma
    // amostra tirada no instante do render pega o valor de partida.
    await expect
      .poll(async () => (await paintedState(page, "[data-focus-key='module-move-up-mod-1']"))?.opacity)
      .toBeLessThan(1);
    const painted = await paintedState(page, "[data-focus-key='module-move-up-mod-1']");
    expect(painted!.cursor).toBe("not-allowed");
  });

  test("o mesmo botão, quando pode agir, continua com cara de vivo", async ({ page }) => {
    await login(page);
    await mockEditorWithModules(page);
    await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=curriculo`);

    const moveUp = page.getByRole("button", { name: "Mover módulo Aprofundamento para cima" });
    await expect(moveUp).toBeEnabled();
    await expect
      .poll(async () => (await paintedState(page, "[data-focus-key='module-move-up-mod-2']"))?.opacity)
      .toBe(1);
    const painted = await paintedState(page, "[data-focus-key='module-move-up-mod-2']");
    expect(painted!.cursor).toBe("pointer");
  });
});
