import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

/**
 * #387 — o alto da vitrine, com e sem capa.
 *
 * Este arquivo existe por um defeito que passou por tipos, lint, testes e
 * build: a capa entrou como novo primeiro filho do hero, e a regra de duas
 * colunas que o hero aplica acima de 680px passou a pôr capa e conteúdo lado a
 * lado. O nome da loja sumia e o logo era cortado. Nada disso aparece na
 * marcação — só em medida.
 *
 * Por isso o teste monta o hero com o CSS de verdade e mede: não sobe
 * servidor, não precisa de banco, e falha se alguém mexer no grid do hero sem
 * olhar o caso com capa.
 */

const CSS = readFileSync(resolve(process.cwd(), "app/styles.css"), "utf8");

/** Um logo quadrado de verdade: o corte só aparece quando ele tem tamanho. */
const PIXEL = `data:image/svg+xml;base64,${Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"><rect width="300" height="300" fill="#0af"/></svg>'
).toString("base64")}`;

function hero(comCapa: boolean) {
  const capa = comCapa
    ? `<div class="storefront-cover"><img class="storefront-cover-image" src="data:image/svg+xml;base64,${Buffer.from(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="500"><rect width="1600" height="500" fill="#123"/></svg>'
      ).toString("base64")}" alt="" width="1600" height="500"></div>`
    : "";
  return `<main class="storefront-main"><section class="${
    comCapa ? "storefront-hero storefront-hero-covered" : "storefront-hero"
  }" aria-labelledby="storefront-title">${capa}<div class="storefront-hero-body">
      <img class="storefront-identity storefront-logo" src="${PIXEL}" alt="Logo de Hub2you" width="300" height="300">
      <div>
        <h1 id="storefront-title">Hub2you</h1>
        <p>IA para corretores de seguros venderem mais</p>
      </div>
    </div></section></main>`;
}

async function medir(page: import("@playwright/test").Page, comCapa: boolean) {
  await page.setContent(`<!doctype html><html lang="pt-BR"><body>${hero(comCapa)}</body></html>`);
  await page.addStyleTag({ content: CSS });
  return page.evaluate(() => {
    const secao = document.querySelector(".storefront-hero") as HTMLElement;
    const caixa = secao.getBoundingClientRect();
    const titulo = document.querySelector("#storefront-title")!.getBoundingClientRect();
    const logo = document.querySelector(".storefront-identity")!.getBoundingClientRect();
    const capa = document.querySelector(".storefront-cover")?.getBoundingClientRect() ?? null;
    return {
      colunas: getComputedStyle(secao).gridTemplateColumns.split(" ").length,
      larguraDaSecao: Math.round(caixa.width),
      titulo: { largura: Math.round(titulo.width), transborda: titulo.right - caixa.right > 1 },
      logo: { largura: Math.round(logo.width), transborda: logo.right - caixa.right > 1 },
      capa: capa ? { largura: Math.round(capa.width), altura: Math.round(capa.height) } : null
    };
  });
}

test.describe("o alto da vitrine", () => {
  test("com capa: uma coluna, nome legível e nada cortado", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const medida = await medir(page, true);

    expect(medida.colunas).toBe(1);
    // A capa vai de borda a borda: os 2px que sobram são a moldura do hero.
    expect(medida.larguraDaSecao - medida.capa!.largura).toBeLessThanOrEqual(2);
    expect(medida.titulo.largura).toBeGreaterThan(100);
    expect(medida.titulo.transborda).toBe(false);
    expect(medida.logo.transborda).toBe(false);
  });

  test("com capa no celular: o mesmo desenho, que é o que já funcionava", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const medida = await medir(page, true);

    expect(medida.colunas).toBe(1);
    expect(medida.titulo.largura).toBeGreaterThan(100);
    expect(medida.logo.transborda).toBe(false);
  });

  test("sem capa: o hero de duas colunas continua de pé", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const medida = await medir(page, false);

    expect(medida.colunas).toBe(2);
    expect(medida.titulo.largura).toBeGreaterThan(100);
    expect(medida.logo.transborda).toBe(false);
  });
});
