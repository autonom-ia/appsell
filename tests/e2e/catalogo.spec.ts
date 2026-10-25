import { expect, test, type Page } from "@playwright/test";
import { login } from "./support/login";

/**
 * #257 — o cartão e a entrada do catálogo.
 *
 * Os dois critérios centrais são medidas, não estrutura: quanto o cartão ocupa
 * de uma tela e qual é o contraste do número que mais importa. Ler a marcação
 * não responde nenhum dos dois, e foi assim que a capa em 16:7 acabou comendo
 * quase um terço da tela sem ninguém notar.
 */

const SELLER_ID = "3a2b1c0d-1111-5222-8333-444455556666";

function products() {
  return [
    {
      id: "11111111-1111-5111-8111-111111111111",
      slug: "ia-na-pratica",
      title: "IA na prática",
      description: "Curso para corretores de seguros que querem usar IA no dia a dia.",
      status: "published",
      created_at: "2026-08-01T00:00:00Z",
      access_mode: "free",
      students: 1,
      course: { id: "c1", title: "Corretores de seguros", status: "published" }
    },
    {
      id: "22222222-2222-5222-8222-222222222222",
      slug: "rascunho",
      title: "Ainda em rascunho",
      description: "",
      status: "draft",
      created_at: "2026-08-02T00:00:00Z",
      access_mode: "free",
      students: 0,
      course: { id: "c2", title: "Fundamentos", status: "draft" }
    }
  ];
}

async function openCatalog(page: Page) {
  await page.route("**/api/sellers", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      json: { sellers: [{ id: SELLER_ID, display_name: "Studio Rodrigo", slug: "studio-rodrigo", role: "seller_owner" }] }
    });
  });
  await page.route(`**/api/sellers/${SELLER_ID}/products*`, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({ json: { products: products(), next_cursor: null } });
  });
  await page.goto("/products");
  await expect(page.locator("article.draft-card").first()).toBeVisible();
}

function luminance(color: string): number {
  const [r, g, b] = (color.match(/\d+(\.\d+)?/g) ?? ["0", "0", "0"]).slice(0, 3).map(Number) as [number, number, number];
  const channel = (value: number) => {
    const proportion = value / 255;
    return proportion <= 0.03928 ? proportion / 12.92 : ((proportion + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(foreground: string, background: string): number {
  const [high, low] = [luminance(foreground), luminance(background)].sort((a, b) => b - a) as [number, number];
  return (high + 0.05) / (low + 0.05);
}

test.describe("#257 — cartão e entrada do catálogo", () => {
  test("B5.1 — o cartão cabe em menos de 40% de uma tela de 800px", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await login(page);
    await openCatalog(page);

    const box = await page.locator("article.draft-card").first().boundingBox();
    expect(box).not.toBeNull();
    // Antes: capa em 16:7 mais nove blocos empilhados. A conta é sobre a altura
    // renderizada, não sobre o número de elementos.
    expect(box!.height).toBeLessThan(800 * 0.4);
  });

  test("B5.2 — a contagem de alunos é maior que o texto de apoio, e legível", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await login(page);
    await openCatalog(page);

    const measured = await page.evaluate(() => {
      const card = document.querySelector("article.draft-card");
      const count = card?.querySelector(".draft-card-count");
      const meta = card?.querySelector(".draft-card-meta");
      if (!count || !meta || !card) return null;
      const countStyle = getComputedStyle(count);
      return {
        countSize: parseFloat(countStyle.fontSize),
        metaSize: parseFloat(getComputedStyle(meta).fontSize),
        color: countStyle.color
      };
    });

    expect(measured).not.toBeNull();
    // Era o menor texto do cartão, em cinza. Agora é o maior.
    expect(measured!.countSize).toBeGreaterThan(measured!.metaSize);
    // O fundo do cartão é um gradiente entre #fff e #f8fbff. Para texto escuro
    // o pior caso é a ponta MAIS ESCURA, não o branco — medir contra branco
    // seria medir o lado otimista e chamar de prova.
    expect(contrast(measured!.color, "rgb(248,251,255)")).toBeGreaterThanOrEqual(4.5);
  });

  test("B5.3 — quem já tem loja não vê o formulário de criar loja", async ({ page }) => {
    await login(page);
    await openCatalog(page);

    await expect(page.getByRole("heading", { name: "Crie sua loja" })).toHaveCount(0);
    await expect(page.getByLabel("Nome do negócio")).toHaveCount(0);
  });

  test("B5.4 — a lista aparece antes de qualquer formulário de criação", async ({ page }) => {
    await login(page);
    await openCatalog(page);

    // Sem clicar em nada, não existe formulário: criar é um botão.
    await expect(page.getByLabel("Nome do produto")).toHaveCount(0);

    await page.getByRole("button", { name: "Novo produto" }).click();
    const order = await page.evaluate(() => {
      const card = document.querySelector("article.draft-card");
      const form = document.querySelector(".product-draft-form");
      if (!card || !form) return null;
      // 4 = DOCUMENT_POSITION_FOLLOWING: o formulário vem depois do cartão.
      return card.compareDocumentPosition(form) & Node.DOCUMENT_POSITION_FOLLOWING;
    });
    expect(order).toBeTruthy();
  });

  test("B5.5 — um só título de primeiro nível na tela", async ({ page }) => {
    await login(page);
    await openCatalog(page);

    await expect(page.locator("h1")).toHaveCount(1);
    await expect(page.locator("h1")).toHaveText("Produtos");
    // E os títulos que competiam com ele sumiram da tela.
    await expect(page.getByRole("heading", { name: "Organize seus produtos" })).toHaveCount(0);
  });

  test("um produto no ar oferece abrir e compartilhar; um rascunho, só abrir", async ({ page }) => {
    await login(page);
    await openCatalog(page);

    const live = page.locator("article.draft-card").filter({ hasText: "IA na prática" });
    await expect(live.getByRole("link", { name: "Abrir" })).toBeVisible();
    await expect(live.getByRole("button", { name: "Compartilhar" })).toBeVisible();

    // Rascunho não tem endereço público, e um botão que copia link quebrado é
    // pior do que botão nenhum.
    const draft = page.locator("article.draft-card").filter({ hasText: "Ainda em rascunho" });
    await expect(draft.getByRole("link", { name: "Abrir" })).toBeVisible();
    await expect(draft.getByRole("button", { name: "Compartilhar" })).toHaveCount(0);
  });

  test("compartilhar copia o endereço público do produto", async ({ page, context, browserName }) => {
    test.skip(browserName === "webkit", "a área de transferência do WebKit não abre por permissão no Playwright");
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await login(page);
    await openCatalog(page);

    await page.locator("article.draft-card").filter({ hasText: "IA na prática" })
      .getByRole("button", { name: "Compartilhar" }).click();

    const copied = await page.evaluate(() => navigator.clipboard.readText());
    // Produto gratuito entra pela página de acesso; mandar para a vitrine faria
    // o link pedir um pagamento que não existe.
    expect(copied).toContain("/s/studio-rodrigo/p/ia-na-pratica/access");
  });
});

test.describe("abrir o formulário de novo produto", () => {
  test("o primeiro campo recebe o foco, em vez de o formulário abrir fora da tela", async ({ page, isMobile }) => {
    test.skip(isMobile === true, "no layout de telefone o formulário já nasce à vista");
    await page.setViewportSize({ width: 1280, height: 800 });
    await login(page);
    await openCatalog(page);

    await page.getByRole("button", { name: "Novo produto" }).click();
    // Sem isto o formulário abre abaixo da dobra e a tela parece não ter
    // reagido ao clique. Focar o primeiro campo rola até ele e já deixa o
    // vendedor digitando — serve para o mouse e para o teclado.
    await expect(page.getByLabel("Nome do produto")).toBeFocused();
  });

  test("nada fica por cima dos campos do formulário", async ({ page, isMobile }) => {
    test.skip(isMobile === true, "a lista grudada no topo só existia no layout de mesa");
    await page.setViewportSize({ width: 1280, height: 800 });
    await login(page);
    await openCatalog(page);

    await page.getByRole("button", { name: "Novo produto" }).click();
    const field = page.getByLabel("Nome do produto");
    await field.scrollIntoViewIfNeeded();
    const box = await field.boundingBox();
    expect(box).not.toBeNull();

    // `.draft-list` continuou `position: sticky` depois que o layout virou uma
    // coluna só e o formulário desceu para depois da lista. A lista ficava
    // colada no topo e sentava em cima dos campos: dava para ver o formulário,
    // não para preencher. Ler a marcação não mostra isso; perguntar quem está
    // no ponto do campo, sim.
    const quemEstaNoPonto = await page.evaluate(({ x, y }) => {
      const node = document.elementFromPoint(x, y) as HTMLElement | null;
      if (!node) return "nada";
      if (node.closest(".draft-list")) return "a lista de produtos";
      return node.tagName.toLowerCase();
    }, { x: box!.x + box!.width / 2, y: box!.y + box!.height / 2 });
    expect(quemEstaNoPonto).toBe("input");
  });
});
