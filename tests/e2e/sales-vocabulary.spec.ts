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

  for (const path of ["/dashboard", "/products", "/payouts"]) {
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
