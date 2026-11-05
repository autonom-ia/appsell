import { expect, test, type Page, type Request } from "@playwright/test";
import { login } from "./support/login";

/**
 * #553, fase 2b — ofertas extras no editor, atrás da lista de lojas da api.
 *
 * Desligado (o caso de toda loja hoje, inclusive a Hub2You), a etapa Preço é a
 * de antes: nenhum painel novo. Ligado, o painel lista as ofertas, usa o
 * ChoiceSelect para moeda e cobrança e manda a troca de padrão com o próprio
 * token CSRF.
 */

const SELLER_ID = "4df98a2e-d1dd-5f25-bbea-58b8072a0e7b";
const PRODUCT_ID = "82efefc5-a3c9-5126-a7d1-4918d5428d63";
const COURSE_ID = "9a4a73d6-bf11-5429-8582-89434000af44";
const DEFAULT_ID = "b1d0c9f6-5a1e-52a4-9c3f-2f0f1d3a7e55";
const EXTRA_ID = "c2e1dae7-6b2f-53b5-8d40-3f1f2e4b8f66";

const DEFAULT_OFFER = {
  id: DEFAULT_ID, title: "Padrão", summary: "", status: "draft", version: 1,
  is_default: true, unit_amount_minor: 9700, currency: "BRL", billing_period: "one_time"
};
const EXTRA_OFFER = {
  ...DEFAULT_OFFER, id: EXTRA_ID, title: "Plano anual", version: 2, is_default: false,
  unit_amount_minor: 19700, billing_period: "yearly"
};

function payload(multiOffer: boolean) {
  return {
    id: PRODUCT_ID,
    seller_organization_id: SELLER_ID,
    seller_slug: "autonomia",
    slug: "ia-na-pratica",
    title: "IA na prática",
    description: "Ofertas",
    status: "draft",
    draft_version: 3,
    course: { id: COURSE_ID, title: "Especialista", description: "", status: "draft", draft_version: 2, position: 1 },
    courses: [],
    offer: {
      id: DEFAULT_ID, title: "Padrão", summary: "", status: "draft", draft_version: 1,
      unit_amount_minor: 9700, currency: "BRL", billing_period: "one_time"
    },
    ...(multiOffer ? { multi_offer_enabled: true, offers: [DEFAULT_OFFER, EXTRA_OFFER] } : {}),
    curriculum: { modules: 1, lessons: 1 },
    publication: null,
    has_unpublished_changes: false,
    readiness: { can_publish: true, has_unpublished_changes: false, items: [] },
    csrf: { update: "u", publish: "p", offer_status: "o", offer_create: "oc", offer_default: "od" }
  };
}

async function openOfferStep(page: Page, multiOffer: boolean) {
  await page.route(`**/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}`, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({ json: payload(multiOffer) });
  });
  await page.route("**/api/sellers/**/curriculum", async (route) => {
    await route.fulfill({ json: { course: { id: COURSE_ID, title: "Especialista" }, modules: [], limits: { modules_per_course: 20, lessons_per_module: 30, lessons_per_course: 200 }, usage: { modules_total: 0, lessons_total: 0, lessons_by_module: {} }, csrf: {} } });
  });
  await page.route("**/access-links", async (route) => {
    await route.fulfill({ json: { access_links: [] } });
  });
  await page.route("**/materials", async (route) => {
    await route.fulfill({ json: { materials: [], csrf: {} } });
  });
  await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=oferta`);
  await expect(page.getByRole("heading", { name: "IA na prática" })).toBeVisible();
  await expect(page.getByRole("group", { name: "Preço e cobrança" })).toBeVisible();
}

/**
 * Erro de página e console.error reprovam o teste: um `<form>` dentro de outro,
 * por exemplo, só aparece como aviso do React no console.
 */
function watchErrors(page: Page, allowedFailedUrl?: string) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const failedLoad = allowedFailedUrl !== undefined
      && message.location().url.includes(allowedFailedUrl)
      && message.text().startsWith("Failed to load resource");
    if (!failedLoad) errors.push(`console.error: ${message.text()}`);
  });
  return errors;
}

function field(body: string, name: string, value: string) {
  return body.includes(`name="${name}"\r\n\r\n${value}\r\n`);
}

test.describe("#553 — ofertas extras atrás da lista de lojas", () => {
  test("desligado: a etapa Preço não ganha nada", async ({ page }) => {
    const errors = watchErrors(page);
    await login(page);
    await openOfferStep(page, false);
    await expect(page.getByRole("heading", { name: "Outros preços" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Tornar principal" })).toHaveCount(0);
    expect(errors).toEqual([]);
  });

  test("ligado: cria um preço pela tela com um POST, sem salvar o rascunho do editor", async ({ page }) => {
    const errors = watchErrors(page);
    await login(page);
    await openOfferStep(page, true);
    const mutations: Request[] = [];
    page.on("request", (request) => {
      if (request.method() !== "GET" && request.url().includes(`/products/${PRODUCT_ID}`)) {
        mutations.push(request);
      }
    });
    await page.route(`**/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}/offers`, async (route) => {
      await route.fulfill({ status: 201, json: { offer_id: "novo", offer_version: 1, created: true } });
    });

    const panel = page.getByRole("region", { name: "Outros preços" });
    await panel.getByLabel("Nome interno").fill("Plano trimestral");
    await panel.getByLabel("Preço").fill("147,50");
    await panel.getByRole("combobox", { name: "Cobrança" }).click();
    await page.getByRole("option", { name: "Assinatura anual" }).click();
    await panel.getByRole("button", { name: "Criar preço em rascunho" }).click();

    await expect.poll(() => mutations.length).toBeGreaterThan(0);
    await expect(panel.getByLabel("Nome interno")).toHaveValue("");
    expect(mutations.map((request) => `${request.method()} ${new URL(request.url()).pathname}`)).toEqual([
      `POST /api/sellers/${SELLER_ID}/products/${PRODUCT_ID}/offers`
    ]);
    const body = mutations[0]!.postDataBuffer()?.toString("utf8") ?? "";
    expect(field(body, "_csrf", "oc")).toBe(true);
    expect(field(body, "title", "Plano trimestral")).toBe(true);
    expect(field(body, "unit_amount_minor", "14750")).toBe(true);
    expect(field(body, "currency", "BRL")).toBe(true);
    expect(field(body, "billing_period", "yearly")).toBe(true);
    expect(errors).toEqual([]);
  });

  test("ligado: lista as ofertas, sem select nativo, e troca a padrão com o próprio token", async ({ page }) => {
    const errors = watchErrors(page);
    await login(page);
    await openOfferStep(page, true);
    const panel = page.getByRole("region", { name: "Outros preços" });
    await expect(panel).toBeVisible();
    await expect(panel.getByRole("list", { name: "Preços" }).getByRole("listitem")).toHaveCount(2);
    await expect(panel.getByRole("combobox", { name: "Moeda" })).toBeVisible();
    await expect(panel.getByRole("combobox", { name: "Cobrança" })).toBeVisible();
    await expect(page.locator("select")).toHaveCount(0);

    let sent: { url: string; csrf: string | null; version: string | null } | null = null;
    await page.route(`**/offers/${EXTRA_ID}/default`, async (route) => {
      const body = route.request().postDataBuffer()?.toString("utf8") ?? "";
      sent = {
        url: route.request().url(),
        csrf: body.includes('name="_csrf"\r\n\r\nod\r\n') ? "od" : null,
        version: body.includes('name="expected_offer_version"\r\n\r\n2\r\n') ? "2" : null
      };
      await route.fulfill({ json: { offer_id: EXTRA_ID, changed: true } });
    });
    await panel.getByRole("button", { name: "Tornar principal" }).click();
    await expect.poll(() => sent).not.toBeNull();
    expect(sent).toMatchObject({ csrf: "od", version: "2" });
    expect(errors).toEqual([]);
  });

  test("ligado: a recusa da api aparece para o vendedor", async ({ page }) => {
    // O 403 aparece no console como recurso que falhou; só essa linha é aceita.
    const errors = watchErrors(page, `/offers/${EXTRA_ID}/default`);
    await login(page);
    await openOfferStep(page, true);
    await page.route(`**/offers/${EXTRA_ID}/default`, async (route) => {
      await route.fulfill({ status: 403, json: { error: "multi_offer_disabled" } });
    });
    const panel = page.getByRole("region", { name: "Outros preços" });
    await panel.getByRole("button", { name: "Tornar principal" }).click();
    await expect(panel.getByRole("alert")).toHaveText("Esta loja ainda não pode ter mais de um preço.");
    expect(errors).toEqual([]);
  });
});
