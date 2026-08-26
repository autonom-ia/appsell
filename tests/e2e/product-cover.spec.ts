import { expect, test, type Page } from "@playwright/test";

/**
 * #232 — a capa do produto, vista pelo vendedor.
 *
 * Três coisas que não dá para conferir lendo o código: que o produto sem capa
 * mostra a arte gerada em vez de um retângulo vazio, que o painel recusa um
 * arquivo do tipo errado antes de gastar a rede, e que a lista aguenta o
 * catálogo cheio sem perder a capa nem a contagem de alunos (A4.6).
 */

const SELLER_ID = "4df98a2e-d1dd-5f25-bbea-58b8072a0e7b";
const PRODUCT_ID = "82efefc5-a3c9-5126-a7d1-4918d5428d63";
const COURSE_ID = "9a4a73d6-bf11-5429-8582-89434000af44";
const OFFER_ID = "b1d0c9f6-5a1e-52a4-9c3f-2f0f1d3a7e55";

// Um PNG 64×64 de um pixel só, o menor arquivo que passa nos limites reais.
const PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAOUlEQVR4nO3BAQ0AAADCoPdPbQ8H"
  + "FAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA4G8DPzAAAWLqjfsAAAAASUVORK5CYII=";

function productPayload(cover: Record<string, unknown> | null) {
  return {
    product: {
      id: PRODUCT_ID,
      seller_organization_id: SELLER_ID,
      seller_slug: "autonomia",
      slug: "ia-na-pratica",
      title: "IA na prática",
      description: "Curso de capa",
      status: "draft",
      draft_version: 3,
      cover,
      course: { id: COURSE_ID, title: "Especialista", description: "", status: "draft", draft_version: 2, position: 1 },
      courses: [],
      offer: { id: OFFER_ID, title: "Oferta padrão", summary: "", status: "draft", draft_version: 1, unit_amount_minor: 4990, currency: "BRL", billing_period: "one_time" },
      curriculum: { modules: 1, lessons: 2 },
      publication: null,
      has_unpublished_changes: true,
      readiness: { can_publish: true, has_unpublished_changes: true, items: [] }
    },
    csrf: {
      update: "csrf-update",
      publish: "csrf-publish",
      offer_status: "csrf-offer",
      cover_intent: "csrf-cover-intent",
      cover_confirm: "csrf-cover-confirm",
      cover_clear: "csrf-cover-clear"
    }
  };
}

async function login(page: Page) {
  await page.goto("/");
  await page.getByRole("link", { name: "Entrar no ambiente local" }).click();
  await expect(page).toHaveURL("/dashboard");
}

async function openEditor(page: Page, cover: Record<string, unknown> | null) {
  await page.route(`**/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}`, async (route) => {
    await route.fulfill({ json: productPayload(cover) });
  });
  await page.route("**/api/sellers/**/curriculum", async (route) => {
    await route.fulfill({ json: { course: { id: COURSE_ID, title: "Especialista" }, modules: [], limits: { modules_per_course: 20, lessons_per_module: 30, lessons_per_course: 200 }, usage: { modules: 0, lessons_total: 0, lessons_by_module: {} }, csrf: {} } });
  });
  await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=produto`);
  await expect(page.getByRole("heading", { name: "Capa do produto" })).toBeVisible();
}

test("um produto sem capa mostra a arte gerada, não um vazio", async ({ page }) => {
  await login(page);
  await openEditor(page, null);

  const fallback = page.locator(".cover-fallback");
  await expect(fallback).toBeVisible();
  await expect(page.locator(".cover-image")).toHaveCount(0);
  // A arte não pode ser um retângulo transparente: se o gradiente sumir, o
  // vendedor vê um buraco na tela e este teste precisa acusar.
  const background = await fallback.evaluate((node) => getComputedStyle(node).backgroundImage);
  expect(background).toContain("gradient");
  await expect(fallback).toContainText("IN");
});

test("uma capa existente aparece com texto alternativo que diz de quem é", async ({ page }) => {
  await login(page);
  await openEditor(page, {
    url: `data:image/png;base64,${PNG_BASE64}`,
    media_type: "image/png",
    width: 64,
    height: 64
  });

  const image = page.getByRole("img", { name: "Capa atual de IA na prática" });
  await expect(image).toBeVisible();
  await expect(page.locator(".cover-fallback")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Remover capa" })).toBeVisible();
});

test("um arquivo do tipo errado é recusado sem chegar à rede", async ({ page }) => {
  await login(page);
  await openEditor(page, null);

  let intentCalls = 0;
  await page.route("**/cover/upload-intents", async (route) => {
    intentCalls += 1;
    await route.fulfill({ json: {} });
  });

  await page.locator('.cover-choose input[type="file"]').setInputFiles({
    name: "planilha.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("a,b\n1,2\n")
  });

  await expect(page.getByText("A capa precisa ser JPEG, PNG ou WebP.")).toBeVisible();
  // O ponto do aviso local é justamente não gastar a rede. Se o intent for
  // chamado, a checagem do navegador não está fazendo o trabalho dela.
  expect(intentCalls).toBe(0);
});

test("A4.6 — o catálogo cheio mantém capa e contagem de alunos em todos os cards", async ({ page }) => {
  await login(page);
  const products = Array.from({ length: 50 }, (_, index) => ({
    id: `8${index.toString().padStart(7, "0")}-a3c9-5126-a7d1-4918d5428d63`,
    slug: `produto-${index}`,
    title: `Produto ${index}`,
    description: "Descrição",
    status: "draft",
    created_at: "2026-08-25T00:00:00Z",
    course: { id: COURSE_ID, title: "Curso", status: "draft" },
    access_mode: "free",
    unit_amount_minor: null,
    currency: null,
    students: index,
    // Metade com capa, metade sem: os dois caminhos precisam desenhar algo.
    cover: index % 2 === 0
      ? { url: `data:image/png;base64,${PNG_BASE64}`, media_type: "image/png", width: 64, height: 64 }
      : null
  }));

  // Um espaço só, para a tela selecioná-lo sozinha e chegar à lista.
  await page.route("**/api/sellers", async (route) => {
    await route.fulfill({
      json: { sellers: [{ id: SELLER_ID, slug: "autonomia", display_name: "Autonom.ia", role: "seller_owner" }] }
    });
  });
  await page.route(`**/api/sellers/${SELLER_ID}/products*`, async (route) => {
    await route.fulfill({ json: { products, next_cursor: null } });
  });

  await page.goto("/products");
  await expect(page.locator("article.draft-card")).toHaveCount(50);
  await expect(page.locator("article.draft-card .draft-card-art")).toHaveCount(50);
  await expect(page.locator("article.draft-card .draft-card-cover")).toHaveCount(25);
  await expect(page.locator("article.draft-card .draft-card-count")).toHaveCount(50);

  const first = page.locator("article.draft-card").first();
  await expect(first.locator(".draft-card-count")).toHaveText("0 alunos");
  await expect(page.locator("article.draft-card").nth(1).locator(".draft-card-count"))
    .toHaveText("1 aluno");
});

test("sair da área durante o envio é bloqueado, e a falha chega aos olhos de quem enviou", async ({ page }) => {
  await login(page);
  await openEditor(page, null);

  // O intent fica pendurado: é assim que o painel permanece "ocupado" enquanto
  // o teste tenta navegar para longe.
  // O executor da Promise roda de imediato, mas a análise de fluxo do
  // TypeScript não enxerga isso e estreitaria `release` para `never`. Guardar o
  // `resolve` numa referência já resolvida evita a asserção que esconderia um
  // contrato quebrado de verdade.
  const release: { fire: () => void } = { fire: () => undefined };
  const held = new Promise<void>((resolve) => { release.fire = resolve; });
  await page.route("**/cover/upload-intents", async (route) => {
    await held;
    await route.fulfill({ status: 503, json: { error: "cover_storage_unavailable" } });
  });

  await page.locator('.cover-choose input[type="file"]').setInputFiles({
    name: "capa.png",
    mimeType: "image/png",
    buffer: Buffer.from(PNG_BASE64, "base64")
  });
  await expect(page.getByText("Enviando a imagem…").or(page.getByText("Preparando o arquivo…"))).toBeVisible();

  await page.getByRole("link", { name: /Conteúdo/ }).first().click();
  // A área não pode trocar: trocar desmontaria o painel e a falha seguinte
  // sumiria sem falar com ninguém.
  await expect(page.getByRole("heading", { name: "Capa do produto" })).toBeVisible();
  await expect(page.getByText(/capa ainda está sendo enviada/i)).toBeVisible();

  release.fire();
  await expect(page.getByText("O envio não terminou. Tente novamente; nada foi alterado.")).toBeVisible();
});

test("A4.2 — as iniciais da arte são legíveis de fato, não só presentes no DOM", async ({ page }) => {
  // Este é o teste que faltava. As iniciais existiam no DOM desde sempre, e um
  // teste de estrutura passava — mas `.draft-card > div > span:last-child` é
  // mais específica do que `.draft-card-art`, então elas renderizavam cinzas,
  // com 10.88px e encostadas na esquerda. Ninguém viu porque ninguém olhou.
  await login(page);
  await page.route("**/api/sellers", async (route) => {
    await route.fulfill({
      json: { sellers: [{ id: SELLER_ID, slug: "autonomia", display_name: "Autonom.ia", role: "seller_owner" }] }
    });
  });
  await page.route(`**/api/sellers/${SELLER_ID}/products*`, async (route) => {
    await route.fulfill({
      json: {
        products: [{
          id: PRODUCT_ID, slug: "ia-na-pratica", title: "IA na prática", description: "Curso",
          status: "draft", created_at: "2026-08-25T00:00:00Z",
          course: { id: COURSE_ID, title: "Curso", status: "draft" },
          access_mode: "free", unit_amount_minor: null, currency: null, students: 3, cover: null
        }],
        next_cursor: null
      }
    });
  });
  await page.goto("/products");

  const art = page.locator("article.draft-card .draft-card-art").first();
  await expect(art).toBeVisible();
  const rendered = await art.evaluate((node) => {
    const span = node.querySelector("span");
    if (!span) return null;
    const style = getComputedStyle(span);
    return {
      texto: span.textContent,
      px: parseFloat(style.fontSize),
      cor: style.color,
      centralizado: getComputedStyle(node).justifyContent
    };
  });

  expect(rendered?.texto).toBe("IN");
  // Grande o bastante para ser lida, branca o bastante para aparecer sobre o
  // gradiente escuro, e no meio do bloco.
  expect(rendered?.px).toBeGreaterThanOrEqual(18);
  expect(rendered?.cor).toBe("rgb(255, 255, 255)");
  expect(rendered?.centralizado).toBe("center");
});
