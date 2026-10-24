import { expect, test, type Page } from "@playwright/test";
import { login } from "./support/login";

/**
 * #256 — criar uma turma é uma decisão guiada, não quatro controles soltos.
 *
 * O que estes testes conferem e a leitura de código não conferiria: que a
 * escolha por chip vira o valor certo **no corpo da requisição**. Um chip que
 * parece selecionado e manda outra coisa é pior do que campo nenhum — e essa
 * ponte entre a tela e o `FormData` só existe em tempo de execução.
 */

const SELLER_ID = "4df98a2e-d1dd-5f25-bbea-58b8072a0e7b";
const PRODUCT_ID = "82efefc5-a3c9-5126-a7d1-4918d5428d63";
const COURSE_ID = "9a4a73d6-bf11-5429-8582-89434000af44";

function productPayload() {
  return {
    id: PRODUCT_ID,
    seller_organization_id: SELLER_ID,
    seller_slug: "autonomia",
    slug: "ia-na-pratica",
    title: "IA na prática",
    description: "Turmas",
    status: "draft",
    draft_version: 3,
    course: { id: COURSE_ID, title: "Especialista", description: "", status: "draft", draft_version: 2, position: 1 },
    courses: [],
    offer: { id: "b1d0c9f6-5a1e-52a4-9c3f-2f0f1d3a7e55", title: "Padrão", summary: "", status: "draft", draft_version: 1, unit_amount_minor: 4990, currency: "BRL", billing_period: "one_time" },
    curriculum: { modules: 1, lessons: 2 },
    publication: { id: "pub", version: 1, published_at: "2026-08-01T00:00:00Z" },
    has_unpublished_changes: false,
    readiness: { can_publish: true, has_unpublished_changes: false, items: [] },
    csrf: { update: "u", publish: "p", offer_status: "o", access_link_create: "c", access_link_revoke: "r", access_link_rotate: "t" }
  };
}

/** Guarda o corpo de cada POST de criação, que é onde a prova mora. */
async function openPanel(page: Page) {
  const sent: Record<string, string>[] = [];
  await page.route(`**/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}`, async (route) => {
    await route.fulfill({ json: productPayload() });
  });
  await page.route("**/api/sellers/**/curriculum", async (route) => {
    await route.fulfill({ json: { course: { id: COURSE_ID, title: "Especialista" }, modules: [], limits: { modules_per_course: 20, lessons_per_module: 30, lessons_per_course: 200 }, usage: { modules_total: 0, lessons_total: 0, lessons_by_module: {} }, csrf: {} } });
  });
  await page.route("**/materials", async (route) => {
    await route.fulfill({ json: { materials: [], csrf: {} } });
  });
  await page.route("**/access-links", async (route) => {
    if (route.request().method() !== "POST") {
      return route.fulfill({ json: { access_links: [] } });
    }
    const raw = route.request().postData() ?? "";
    const fields: Record<string, string> = {};
    // multipart: o nome vem no Content-Disposition e o valor depois da linha em
    // branco, até o próximo delimitador. Percorrer o corpo inteiro em vez de
    // cortá-lo por delimitador é o que faz um valor vazio ("sem limite", "sem
    // prazo") aparecer como "" e não sumir — e vazio é justamente a escolha que
    // estes testes precisam distinguir.
    for (const part of raw.matchAll(/name="([^"]+)"\r?\n\r?\n([\s\S]*?)\r?\n-{2,}/g)) {
      fields[part[1]!] = part[2]!;
    }
    sent.push(fields);
    await route.fulfill({ json: { url: "https://exemplo.test/t/abc" } });
  });
  await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=publicar`);
  await expect(page.getByRole("heading", { name: "Turmas", exact: true })).toBeVisible();
  return sent;
}

test.describe("#256 — turmas", () => {
  test("B4.1 — vagas por chip chega como o número escolhido", async ({ page }) => {
    await login(page);
    const sent = await openPanel(page);

    await page.getByLabel("Nome da turma").fill("Turma de agosto");
    await page.getByRole("radio", { name: "50", exact: true }).check();
    await page.getByRole("button", { name: "Criar turma" }).click();

    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0]!.max_uses).toBe("50");
    expect(sent[0]!.label).toBe("Turma de agosto");
  });

  test("B4.1 — sem limite manda vazio, que é como a rota lê 'ilimitado'", async ({ page }) => {
    await login(page);
    const sent = await openPanel(page);

    await page.getByLabel("Nome da turma").fill("Turma aberta");
    await page.getByRole("radio", { name: "Sem limite" }).check();
    await page.getByRole("button", { name: "Criar turma" }).click();

    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0]!.max_uses).toBe("");
  });

  test("B4.1 — entrada livre fora da faixa não chega a sair da tela", async ({ page }) => {
    await login(page);
    const sent = await openPanel(page);

    await page.getByLabel("Nome da turma").fill("Turma grande");
    await page.getByRole("radio", { name: "Outro número" }).check();
    await page.getByLabel("Número exato").pressSequentially("200000");
    await page.getByRole("button", { name: "Criar turma" }).click();

    await expect(page.getByRole("alert").filter({ hasText: "100.000" })).toBeVisible();
    // Nenhuma requisição: o inválido para na interface, não no servidor.
    expect(sent).toHaveLength(0);
  });

  test("B4.2 — o prazo por chip vira uma data", async ({ page }) => {
    await login(page);
    const sent = await openPanel(page);

    await page.getByLabel("Nome da turma").fill("Turma de sete dias");
    await page.getByRole("radio", { name: "7 dias" }).check();
    const before = Date.now();
    await page.getByRole("button", { name: "Criar turma" }).click();

    await expect.poll(() => sent.length).toBe(1);
    const sevenDays = 7 * 24 * 60 * 60 * 1000;
    const parsed = Date.parse(sent[0]!.expires_at!);
    // Uma folga de um minuto cobre o tempo entre a leitura acima e o clique,
    // sem afrouxar a asserção a ponto de aceitar trinta dias no lugar de sete.
    expect(parsed).toBeGreaterThanOrEqual(before + sevenDays - 60_000);
    expect(parsed).toBeLessThan(before + sevenDays + 60_000);
  });

  test("B4.2 — sem prazo é uma escolha, e vai como vazio", async ({ page }) => {
    await login(page);
    const sent = await openPanel(page);

    await page.getByLabel("Nome da turma").fill("Turma sem prazo");
    await page.getByRole("radio", { name: "Sem prazo" }).check();
    await page.getByRole("button", { name: "Criar turma" }).click();

    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0]!.expires_at).toBe("");
  });

  test("B4.2 — a data escolhida vale até o fim do dia, não até o começo", async ({ page }) => {
    await login(page);
    const sent = await openPanel(page);

    await page.getByLabel("Nome da turma").fill("Turma com data");
    await page.getByRole("radio", { name: "Escolher data" }).check();
    const day = new Date(Date.now() + 3 * 24 * 60 * 60 * 1000);
    const iso = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
    await page.getByLabel("Último dia").fill(iso);
    await page.getByRole("button", { name: "Criar turma" }).click();

    await expect.poll(() => sent.length).toBe(1);
    const closes = new Date(sent[0]!.expires_at!);
    expect(closes.getDate()).toBe(day.getDate());
    // Meia-noite trancaria do lado de fora quem entrasse no dia escolhido.
    expect(closes.getHours()).toBe(23);
  });

  test("B4.2 — o campo de data pede dia, não hora e minuto", async ({ page }) => {
    await login(page);
    await openPanel(page);

    await page.getByRole("radio", { name: "Escolher data" }).check();
    // `datetime-local` era o campo antigo: pedia precisão de minuto para
    // fechar uma turma.
    await expect(page.getByLabel("Último dia")).toHaveAttribute("type", "date");
  });

  test("B4.3 — o motivo fica visível antes de acionar, e o botão continua vivo", async ({ page }) => {
    await login(page);
    await openPanel(page);

    // A #254 travou que botão não desliga em silêncio. Aqui o motivo aparece
    // sem precisar acionar — que é o que a #256 pediu, sem voltar a desligar.
    await expect(page.getByText("Falta o nome da turma.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Criar turma" })).toBeEnabled();

    await page.getByLabel("Nome da turma").fill("Turma de agosto");
    await expect(page.getByText("Falta o nome da turma.")).toHaveCount(0);
  });

  test("B4.3 — escolher 'Outro número' sem preencher também diz o que falta", async ({ page }) => {
    await login(page);
    const sent = await openPanel(page);

    await page.getByLabel("Nome da turma").fill("Turma de agosto");
    await page.getByRole("radio", { name: "Outro número" }).check();
    await expect(page.getByText("Informe quantas vagas.")).toBeVisible();

    await page.getByRole("button", { name: "Criar turma" }).click();
    await expect(page.getByLabel("Número exato")).toBeFocused();
    expect(sent).toHaveLength(0);
  });

  test("B4.3 — com dois campos pendentes, o foco vai para o que a tela nomeia", async ({ page }) => {
    // A ordem do motivo e a ordem do foco precisam ser a mesma. Estavam
    // trocadas: a tela dizia "informe quantas vagas" e o cursor caía no campo
    // de data.
    await login(page);
    const sent = await openPanel(page);

    await page.getByLabel("Nome da turma").fill("Turma de agosto");
    await page.getByRole("radio", { name: "Outro número" }).check();
    await page.getByRole("radio", { name: "Escolher data" }).check();

    await expect(page.getByText("Informe quantas vagas.")).toBeVisible();
    await page.getByRole("button", { name: "Criar turma" }).click();
    await expect(page.getByLabel("Número exato")).toBeFocused();
    expect(sent).toHaveLength(0);
  });

  test("B4.4 — a lista mostra usadas, restantes e estado sem passar o mouse", async ({ page }) => {
    await login(page);
    await page.route(`**/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}`, async (route) => {
      await route.fulfill({ json: productPayload() });
    });
    await page.route("**/api/sellers/**/curriculum", async (route) => {
      await route.fulfill({ json: { course: { id: COURSE_ID, title: "Especialista" }, modules: [], limits: { modules_per_course: 20, lessons_per_module: 30, lessons_per_course: 200 }, usage: { modules_total: 0, lessons_total: 0, lessons_by_module: {} }, csrf: {} } });
    });
    await page.route("**/materials", async (route) => {
      await route.fulfill({ json: { materials: [], csrf: {} } });
    });
    await page.route("**/access-links", async (route) => {
      await route.fulfill({
        json: {
          access_links: [{
            access_link_id: "a1", token_prefix: "tur_abc", label: "Turma de julho",
            max_uses: 25, redeemed_count: 7, expires_at: null, revoked_at: null,
            rotated_at: null, created_at: "2026-07-01T00:00:00Z"
          }]
        }
      });
    });
    await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=publicar`);

    const card = page.locator(".access-link-card");
    await expect(card).toBeVisible();
    const text = await card.innerText();
    expect(text).toContain("7 de 25 vagas usadas");
    expect(text).toContain("18 restantes");
    expect(text).toContain("Ativo");
  });
});
