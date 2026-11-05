import { expect, test, type Page } from "@playwright/test";
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
  await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=oferta`);
  await expect(page.getByRole("heading", { name: "IA na prática" })).toBeVisible();
  await expect(page.getByRole("group", { name: "Preço e cobrança" })).toBeVisible();
}

test.describe("#553 — ofertas extras atrás da lista de lojas", () => {
  test("desligado: a etapa Preço não ganha nada", async ({ page }) => {
    await login(page);
    await openOfferStep(page, false);
    await expect(page.getByRole("heading", { name: "Outras ofertas" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Tornar padrão" })).toHaveCount(0);
  });

  test("ligado: lista as ofertas, sem select nativo, e troca a padrão com o próprio token", async ({ page }) => {
    await login(page);
    await openOfferStep(page, true);
    const panel = page.getByRole("region", { name: "Outras ofertas" });
    await expect(panel).toBeVisible();
    await expect(panel.getByRole("list", { name: "Ofertas" }).getByRole("listitem")).toHaveCount(2);
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
    await panel.getByRole("button", { name: "Tornar padrão" }).click();
    await expect.poll(() => sent).not.toBeNull();
    expect(sent).toMatchObject({ csrf: "od", version: "2" });
  });

  test("ligado: a recusa da api aparece para o vendedor", async ({ page }) => {
    await login(page);
    await openOfferStep(page, true);
    await page.route(`**/offers/${EXTRA_ID}/default`, async (route) => {
      await route.fulfill({ status: 403, json: { error: "multi_offer_disabled" } });
    });
    const panel = page.getByRole("region", { name: "Outras ofertas" });
    await panel.getByRole("button", { name: "Tornar padrão" }).click();
    await expect(panel.getByRole("alert")).toHaveText("Esta loja ainda não pode ter mais de uma oferta.");
  });
});
