import { expect, test, type Page } from "@playwright/test";

/**
 * A3.5 — percorrer o editor e publicar usando apenas o teclado, com o foco
 * sempre visível.
 *
 * Este critério não pode ser conferido lendo o código: depende de ordem de
 * tabulação, de o foco realmente ir para onde o diálogo diz, e de o anel de
 * foco existir de fato. Por isso o teste dirige a tela real e afirma, a cada
 * parada, quem está com o foco e se ele é visível.
 */

// A3.5 fala de teclado. O perfil móvel não tabula, e rodar isto lá produziria
// uma falha que não diz nada sobre o critério — ou, pior, uma asserção afrouxada
// para caber nos dois. O critério de toque é outro e tem os seus próprios testes.
test.skip(({ isMobile }) => isMobile === true, "percurso por teclado não se aplica ao perfil móvel");

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
    description: "Curso de teclado",
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
  // O currículo e os materiais não são o objeto deste teste; respondem vazio
  // para a área abrir sem depender do domínio.
  await page.route("**/api/sellers/**/curriculum", async (route) => {
    await route.fulfill({ json: { course: { id: COURSE_ID, title: "Especialista" }, modules: [], limits: { modules_per_course: 20, lessons_per_module: 30, lessons_per_course: 200 }, usage: { modules: 0, lessons_total: 0, lessons_by_module: {} }, csrf: {} } });
  });
  await page.route("**/materials", async (route) => {
    await route.fulfill({ json: { materials: [], csrf: { create: "c", update: "u", reorder: "r", archive: "a" } } });
  });
}

/**
 * Quem está com o foco agora, e se o anel está mesmo desenhado.
 *
 * Deliberadamente NÃO consulta `:focus-visible`: ele casa depois de um
 * `.focus()` programático mesmo quando o CSS não desenha anel nenhum, e uma
 * versão anterior deste teste passava com o anel removido por causa disso. O
 * que conta é a largura computada do contorno, e só ela.
 */
async function focusReport(page: Page) {
  return await page.evaluate(() => {
    const active = document.activeElement as HTMLElement | null;
    if (!active || active === document.body) return { tag: "", label: "", visible: false };
    const style = getComputedStyle(active);
    // Só o contorno conta. `box-shadow` não serve de prova: o botão secundário
    // desta aplicação tem sombra permanente, então aceitá-la deixava a asserção
    // passar com o anel de foco removido.
    const ring = style.outlineStyle !== "none" && parseFloat(style.outlineWidth || "0") > 0;
    return {
      tag: active.tagName.toLowerCase(),
      label: (active.getAttribute("aria-label") || active.textContent || "").trim().slice(0, 60),
      visible: ring
    };
  });
}

/**
 * Anda com Tab até o rótulo pedido. Usar Tab e não `.focus()` é o ponto: a
 * modalidade de entrada é o que faz o navegador desenhar o anel, então só assim
 * o teste percorre a tela como um vendedor de teclado percorreria.
 */
async function tabTo(page: Page, pattern: RegExp, limit = 40) {
  for (let step = 0; step < limit; step += 1) {
    await page.keyboard.press("Tab");
    const report = await focusReport(page);
    if (pattern.test(report.label)) return report;
  }
  throw new Error(`não cheguei em ${pattern} com ${limit} tabulações`);
}

test("percorre as áreas e publica somente com o teclado", async ({ page }) => {
  await login(page);
  await mockEditor(page);
  await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}`);
  await expect(page.getByRole("heading", { name: "IA na prática" })).toBeVisible();

  // As áreas são alcançáveis por tabulação, e não há ordem obrigatória entre
  // elas: dá para ir direto de Produto a Publicar.
  const areaNav = page.getByRole("navigation", { name: "Áreas do produto" });
  await expect(areaNav.getByRole("link")).toHaveCount(4);

  await page.locator("body").click({ position: { x: 1, y: 1 } });
  const onArea = await tabTo(page, /Distribuição/);
  expect(onArea.tag).toBe("a");
  expect(onArea.visible).toBe(true);

  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Publicar catálogo" })).toBeVisible();
  await expect(page).toHaveURL(/section=publicar/);

  // O botão de publicar é alcançável e o foco continua visível nele.
  const publishButton = page.getByRole("button", { name: /Publicar catálogo/ });
  await expect(publishButton).toBeEnabled();
  const onPublish = await tabTo(page, /Publicar catálogo/);
  expect(onPublish.tag).toBe("button");
  expect(onPublish.visible).toBe(true);

  // Enter abre a confirmação, e o foco entra no diálogo sem o mouse.
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  const inDialog = await page.evaluate(() => {
    const active = document.activeElement;
    return active ? Boolean(active.closest("[role='dialog']")) : false;
  });
  expect(inDialog).toBe(true);

  // Escape devolve o foco a quem abriu, que é o que permite continuar no teclado.
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  const returned = await focusReport(page);
  expect(returned.label).toContain("Publicar catálogo");
});

test("abre e fecha Ver como aluno pelo teclado, de qualquer área", async ({ page }) => {
  await login(page);
  await mockEditor(page);
  await page.route("**/api/sellers/**/preview", async (route) => {
    await route.fulfill({ json: { preview: { product: { title: "IA na prática" }, course: { title: "Especialista" }, courses: [] } } });
  });
  await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=produto`);

  const trigger = page.getByRole("button", { name: "Ver como aluno" });
  await expect(trigger).toBeVisible();
  const onTrigger = await tabTo(page, /Ver como aluno/);
  expect(onTrigger.tag).toBe("button");
  expect(onTrigger.visible).toBe(true);

  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Ver como aluno" })).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  const returned = await focusReport(page);
  expect(returned.label).toContain("Ver como aluno");
});

test("um link antigo de prévia abre o editor em Produto, sem erro", async ({ page }) => {
  // A3.6: a etapa deixou de existir, mas o endereço antigo continua circulando.
  await login(page);
  await mockEditor(page);
  await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=preview`);

  await expect(page.getByRole("heading", { name: "IA na prática" })).toBeVisible();
  const produto = page.getByRole("navigation", { name: "Áreas do produto" })
    .getByRole("link", { name: /Produto/ });
  await expect(produto).toHaveAttribute("aria-current", "page");
});
