import { expect, test, type Page } from "@playwright/test";
import { login } from "./support/login";

/**
 * #546 — preço em que a loja não recebe nada.
 *
 * Quem decide o mínimo é o servidor (decisão 005): a rota devolve 422 com
 * `minimum_minor`, e a tela só repete esse valor em reais. Este arquivo
 * intercepta o 422 e confere o que a pessoa vê, e para onde vai o foco.
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
    description: "Curso com preço",
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

async function mockEditor(page: Page, onPatch: () => void) {
  await page.route(`**/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}`, async (route) => {
    if (route.request().method() === "PATCH") {
      onPatch();
      await route.fulfill({
        status: 422,
        json: {
          error: "invalid_product_input",
          field: "offer_unit_amount_minor",
          reason: "below_minimum",
          minimum_minor: 52
        }
      });
      return;
    }
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

test.describe("#546 — preço abaixo do mínimo", () => {
  test("salvar R$ 0,51 mostra o menor preço possível no campo e leva o foco a ele", async ({ page }) => {
    let patches = 0;
    await login(page);
    await mockEditor(page, () => { patches += 1; });
    await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=oferta`);

    const price = page.locator("#offer_price");
    await expect(price).toBeVisible();
    await price.fill("0,51");
    await page.getByRole("button", { name: "Salvar rascunho" }).click();

    // Pega: a recusa do servidor virando o texto genérico "Informe um preço
    // válido", que não diz à pessoa qual preço funciona.
    const message = page.locator("#offer_price-error");
    await expect(message).toBeVisible();
    // O preço sai com espaço não separável (R$\u00a00,52); a comparação usa o comum.
    const shown = ((await message.textContent()) ?? "").replaceAll("\u00a0", " ");
    expect(shown).toBe(
      "Com esse preço, a taxa da plataforma fica com todo o valor e a loja não recebe nada. O menor preço possível é R$ 0,52."
    );
    await expect(message).toHaveAttribute("role", "alert");
    await expect(price).toHaveAttribute("aria-invalid", "true");
    expect((await price.getAttribute("aria-describedby")) ?? "").toContain("offer_price-error");
    // Pega: o foco procurando um campo com o nome do servidor, que não existe na tela.
    await expect(price).toBeFocused();
    expect(patches).toBe(1);
  });
});
