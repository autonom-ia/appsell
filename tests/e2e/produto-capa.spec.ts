import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

/**
 * #387 — a capa do produto no cartão da loja.
 *
 * A mesma imagem 16:9 caía em caixas de proporções diferentes e era cortada em
 * todas, sem que nada disso estivesse anunciado ao vendedor. Agora pedimos
 * 16:9 no painel e o cartão respeita essa proporção. O teste mede com o CSS de
 * verdade: se alguém voltar a espremer a capa, cai aqui.
 *
 * A página de vendas mantém banner e capa em papéis separados, cada um na sua
 * proporção, e ambos ganham testes abaixo.
 */

const CSS = readFileSync(resolve(process.cwd(), "app/styles.css"), "utf8");

/** Uma capa 16:9 de verdade: o corte só aparece quando a imagem tem proporção. */
const CAPA = `data:image/svg+xml;base64,${Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900"><rect width="1600" height="900" fill="#123"/></svg>'
).toString("base64")}`;

/** O banner que pedimos para a página de vendas. */
const BANNER = `data:image/svg+xml;base64,${Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="500"><rect width="1600" height="500" fill="#123"/></svg>'
).toString("base64")}`;

const PAGINA_DE_VENDAS = `<div class="storefront-page product-checkout-page"><main class="product-checkout-main lp-main">
  <section class="product-checkout-summary lp-hero">
    <div class="lp-banner"><img class="lp-banner-image" src="${BANNER}" alt="" width="1600" height="500"></div>
    <p class="eyebrow">Hub2you</p>
    <h1>IA na prática</h1>
    <p class="product-checkout-price">R$ 47,00</p>
  </section>
</main></div>`;

/** O mesmo produto antes de ter banner: aí a capa abre a página. */
const PAGINA_SEM_BANNER = `<div class="storefront-page product-checkout-page"><main class="product-checkout-main lp-main">
  <section class="product-checkout-summary lp-hero">
    <img class="lp-cover" src="${CAPA}" alt="" width="1600" height="900">
    <p class="eyebrow">Hub2you</p>
    <h1>IA na prática</h1>
    <p class="product-checkout-price">R$ 47,00</p>
  </section>
</main></div>`;

const CARTAO_DA_LOJA = `<div class="storefront-page"><main><ul class="storefront-products"><li class="storefront-product">
  <div class="storefront-product-art">
    <img class="storefront-product-cover" src="${CAPA}" alt="" width="1600" height="900">
  </div>
  <div><h2>IA na prática</h2></div>
</li></ul></main></div>`;

async function proporcoes(page: import("@playwright/test").Page, html: string, seletor: string) {
  await page.setContent(`<!doctype html><html lang="pt-BR"><body>${html}</body></html>`);
  await page.addStyleTag({ content: CSS });
  return page.evaluate((alvo) => {
    const caixa = document.querySelector(alvo)!.getBoundingClientRect();
    const img = document.querySelector(`${alvo} img, img${alvo}`)! as HTMLImageElement;
    const titulo = document.querySelector("h1");
    return {
      daCaixa: Number((caixa.width / caixa.height).toFixed(2)),
      daImagem: Number((img.naturalWidth / img.naturalHeight).toFixed(2)),
      altura: Math.round(caixa.height),
      largura: Math.round(caixa.width),
      larguraDoTitulo: titulo ? Math.round(titulo.getBoundingClientRect().width) : null
    };
  }, seletor);
}

test.describe("a capa do produto não é espremida", () => {
  test("no cartão da loja", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const medida = await proporcoes(page, CARTAO_DA_LOJA, ".storefront-product-art");

    expect(medida.daCaixa).toBeCloseTo(medida.daImagem, 1);
  });

  test("no cartão da loja, no celular", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const medida = await proporcoes(page, CARTAO_DA_LOJA, ".storefront-product-art");

    expect(medida.daCaixa).toBeCloseTo(medida.daImagem, 1);
  });

  test("na página de vendas, quando ela abre a página", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const medida = await proporcoes(page, PAGINA_SEM_BANNER, ".lp-cover");

    expect(medida.daCaixa).toBeCloseTo(medida.daImagem, 1);
  });
});

test.describe("o alto da página de vendas mostra uma imagem, nunca duas", () => {
  test("com banner, a capa não aparece junto", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.setContent(`<!doctype html><html lang="pt-BR"><body>${PAGINA_DE_VENDAS}</body></html>`);
    await page.addStyleTag({ content: CSS });

    await expect(page.locator(".lp-banner")).toHaveCount(1);
    await expect(page.locator(".lp-cover")).toHaveCount(0);
  });

  test("sem banner, a capa ocupa o lugar dele", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.setContent(`<!doctype html><html lang="pt-BR"><body>${PAGINA_SEM_BANNER}</body></html>`);
    await page.addStyleTag({ content: CSS });

    await expect(page.locator(".lp-banner")).toHaveCount(0);
    await expect(page.locator(".lp-cover")).toHaveCount(1);
  });
});

test.describe("o banner abre a página de vendas sem corte", () => {
  for (const [tela, largura] of [
    ["computador", 1440],
    ["tablet", 768],
    ["celular", 390]
  ] as const) {
    test(`no ${tela}`, async ({ page }) => {
      await page.setViewportSize({ width: largura, height: 900 });
      const medida = await proporcoes(page, PAGINA_DE_VENDAS, ".lp-banner");

      expect(medida.daCaixa).toBeCloseTo(medida.daImagem, 1);
      expect(medida.largura).toBe(medida.larguraDoTitulo);
    });
  }
});
