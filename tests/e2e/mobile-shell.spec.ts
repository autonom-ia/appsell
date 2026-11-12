import { expect, test } from "@playwright/test";
import { E2E_BUILD_PUBLIC_ORIGIN } from "../../lib/e2e-build-runtime";
import { login } from "./support/login";
import { E2E_STORAGE_ORIGIN } from "./support/storage";

const SHELL_BRAND_NAME = "sell. by Autonom.ia: início";

async function enterAndWaitForSession(page: import("@playwright/test").Page) {
  const sessionReady = page.waitForResponse((response) =>
    response.url().endsWith("/api/session") && response.status() === 200);
  await login(page);
  await expect(page.getByRole("heading", { name: "Seu negócio em um só lugar." })).toBeVisible();
  await sessionReady;
}

test("keeps the core journey usable without horizontal overflow", async ({ page }) => {
  await enterAndWaitForSession(page);
  await expect(page.getByRole("link", { name: SHELL_BRAND_NAME }))
    .toContainText("sell.");
  for (const target of [
    { path: "/dashboard", heading: "Seu negócio em um só lugar." },
    { path: "/products", heading: "Produtos" },
    { path: "/library", heading: "Minha biblioteca" },
  ]) {
    if (page.url() !== `${E2E_BUILD_PUBLIC_ORIGIN}${target.path}`) {
      await page.locator(`a[href="${target.path}"]:visible`).first().click();
      await expect(page).toHaveURL(target.path, { timeout: 20_000 });
      await expect(page.getByRole("heading", { name: target.heading, exact: true })).toBeVisible();
    }
    const dimensions = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
    expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client);
  }
  await expect(page.getByRole("heading", { name: "Minha biblioteca" })).toBeVisible();
  await expect(page.getByText("A biblioteca não respondeu. Tente novamente.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Tentar novamente" })).toBeVisible();
});

test("does not render protected preview content for an anonymous request", async ({ request }) => {
  const response = await request.get("/dashboard", {
    maxRedirects: 0,
    headers: { "x-appsell-demo-persona": "buyer" }
  });
  expect(response.status()).toBe(307);
  expect(response.headers().location).toBe("/");
  const body = await response.text();
  expect(body).not.toContain("Seu negócio em um só lugar.");
  expect(body).not.toContain("Creator OS");
});

test("exposes touch-sized mobile navigation and preserves deep links", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await enterAndWaitForSession(page);
  const mobileBrand = page.locator(".mobile-topbar")
    .getByRole("link", { name: SHELL_BRAND_NAME });
  await expect(mobileBrand).toContainText("sell.");
  const brandBox = await mobileBrand.boundingBox();
  expect(brandBox?.x ?? 321).toBeGreaterThanOrEqual(0);
  expect((brandBox?.x ?? 321) + (brandBox?.width ?? 321)).toBeLessThanOrEqual(320);
  const libraryLink = page.getByRole("navigation", { name: "Navegação móvel" }).getByRole("link", { name: "Minha biblioteca" });
  const box = await libraryLink.boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  await libraryLink.click();
  await expect(page).toHaveURL("/library");
  expect((await page.request.get("/api/session")).status()).toBe(200);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Minha biblioteca" })).toBeVisible();
  await page.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
  const zoomedTopbar = await page.locator(".mobile-topbar").evaluate((element) => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
    children: [...element.children].map((child) => {
      const rect = child.getBoundingClientRect();
      return { left: rect.left, right: rect.right, width: rect.width, height: rect.height };
    })
  }));
  expect(zoomedTopbar.scroll).toBeLessThanOrEqual(zoomedTopbar.client);
  expect(zoomedTopbar.children.every((rect) =>
    rect.left >= -1 && rect.right <= zoomedTopbar.client + 1 && rect.height >= 44
  )).toBe(true);
});

test("supports keyboard skip navigation and reduced motion", async ({ page }) => {
  await enterAndWaitForSession(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.reload();
  await expect(page.getByRole("heading", { name: "Seu negócio em um só lugar." })).toBeVisible();
  const skipLink = page.getByRole("link", { name: "Pular para o conteúdo" });
  await skipLink.focus();
  await expect(skipLink).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#conteudo")).toBeFocused();
  const transitionDuration = await page.locator(".primary-button").first().evaluate((element) => getComputedStyle(element).transitionDuration);
  expect(Number.parseFloat(transitionDuration)).toBeLessThanOrEqual(0.001);
});

test("reuses the resolved shell profile across client-side navigation", async ({ page }) => {
  await enterAndWaitForSession(page);
  let sessionRequests = 0;
  page.on("response", (response) => {
    if (response.url().endsWith("/api/session")) sessionRequests += 1;
  });
  await page.locator('a[href="/products"]:visible').first().click();
  await expect(page.getByRole("heading", { name: "Produtos", exact: true })).toBeVisible();
  expect(sessionRequests).toBe(0);
  await expect(page.locator(".session-loading")).toHaveCount(0);
});

test("keeps seller navigation hidden until the shell profile resolves", async ({ page }) => {
  let releaseSession: (() => void) | undefined;
  const sessionReleased = new Promise<void>((resolve) => { releaseSession = resolve; });
  await page.route("**/api/session", async (route) => {
    await sessionReleased;
    await route.continue();
  });

  await login(page);
  await expect(page.getByRole("heading", { name: "Seu negócio em um só lugar." })).toBeVisible();
  await expect(page.getByRole("link", { name: "Produtos" })).toHaveCount(0);
  await expect(page.locator(".nav-eyebrow")).toHaveText("Carregando acesso");
  await expect(page.getByRole("navigation", { name: "Navegação móvel" }).getByRole("link")).toHaveCount(0);

  releaseSession?.();
  await expect(page.getByRole("link", { name: "Produtos" }).first()).toBeVisible();
});

test("keeps seller navigation hidden when the shell profile is unavailable", async ({ page }) => {
  await page.route("**/api/session", async (route) => {
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ authenticated: false }) });
  });

  await login(page);
  await expect(page.locator(".nav-eyebrow")).toHaveText("Acesso indisponível");
  await expect(page.getByRole("link", { name: "Produtos" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /Voltar para a entrada|Entrada/ }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Tentar novamente" })).toBeVisible();
});

test("keeps seller onboarding recoverable when the domain API is unavailable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await enterAndWaitForSession(page);
  // O admin demo não pode criar loja de verdade: a rota recusa seller.onboard
  // com 403 antes de chegar à API (requireDemoCapability, em
  // lib/internal-api-client.server.ts). O que este teste prova é a tela diante
  // do 503 da API caída, então só o POST é simulado; a leitura continua real.
  await page.route("**/api/sellers", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await route.fulfill({ status: 503, json: { error: "seller_api_unavailable" } });
  });
  await page.goto("/products");
  const submit = page.getByRole("button", { name: "Ativar minha loja" });
  await expect(submit).toBeEnabled();
  await expect(page.getByText("A API está indisponível.")).toBeVisible();
  await page.getByLabel("Nome do negócio").fill("Studio Mobile");
  await page.getByLabel("Endereço público").fill("studio-mobile");
  await submit.click();
  await expect(page.getByText("A API está indisponível.")).toBeVisible();
  await expect(submit).toBeEnabled();
  await expect(page.getByLabel("Nome do negócio")).toHaveValue("Studio Mobile");
});

test("creates a tenant-scoped product draft in the mobile UI", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "interactive product flow runs once in Chromium");
  const sellerId = "52000000-0000-4000-8000-000000000001";
  const otherSellerId = "52000000-0000-4000-8000-000000000002";
  const products: Array<Record<string, unknown>> = [{ id: "53000000-0000-4000-8000-000000000000", slug: "draft-existente", title: "Draft existente", description: "Já persistido.", status: "draft", created_at: "2026-07-11T11:00:00Z", course: { id: "54000000-0000-4000-8000-000000000000", title: "Curso existente", status: "draft" } }];
  products.push({ id: "53000000-0000-4000-8000-000000000010", slug: "publicado", title: "Produto publicado", description: "Snapshot ativo.", status: "published", created_at: "2026-07-11T10:00:00Z", course: { id: "54000000-0000-4000-8000-000000000010", title: "Curso publicado", status: "published" } }, { id: "53000000-0000-4000-8000-000000000020", slug: "despublicado", title: "Produto despublicado", description: "Fora da vitrine.", status: "unpublished", created_at: "2026-07-11T09:00:00Z", course: { id: "54000000-0000-4000-8000-000000000020", title: "Curso despublicado", status: "draft" } });
  let releaseCreate: (() => void) | undefined;
  let markCreateStarted: (() => void) | undefined;
  const createStarted = new Promise<void>((resolve) => { markCreateStarted = resolve; });

  await page.route("**/api/sellers", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        sellers: [
          { id: sellerId, slug: "studio-mobile", display_name: "Studio Mobile", role: "seller_owner" },
          { id: otherSellerId, slug: "studio-b", display_name: "Studio B", role: "seller_owner" }
        ]
      })
    });
  });
  await page.route(`**/api/sellers/${sellerId}/products`, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ products, next_cursor: null })
      });
      return;
    }
    const posted = route.request().postDataBuffer();
    const input = await new Request(route.request().url(), {
      method: "POST",
      headers: route.request().headers(),
      body: posted ? new Uint8Array(posted) : undefined
    }).formData();
    expect(input.get("_csrf")).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(input.get("idempotency_key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(input.get("slug")).toBe("creator-os-mobile");
    markCreateStarted?.();
    await new Promise<void>((resolve) => { releaseCreate = resolve; });
    products.push({
      id: "53000000-0000-4000-8000-000000000001",
      slug: input.get("slug"),
      title: input.get("product_title"),
      description: input.get("product_description"),
      status: "draft",
      created_at: "2026-07-11T12:00:00Z",
      course: {
        id: "54000000-0000-4000-8000-000000000001",
        title: input.get("course_title"),
        status: "draft"
      }
    });
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ product_id: products[0]?.id, status: "draft", created: true })
    });
  });

  await page.setViewportSize({ width: 360, height: 800 });
  await enterAndWaitForSession(page);
  await page.goto("/products");
  await page.getByLabel("Sua loja").selectOption({ label: "Studio Mobile · @studio-mobile" });
  // A lista vem antes do formulário desde a #257: quem chega em Produtos vê o
  // que já tem, e criar é um botão.
  await expect(page.getByText("Publicado", { exact: true })).toBeVisible();
  await expect(page.getByText("Despublicado", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Abrir" }).first()).toBeVisible();
  await expect(page.getByLabel("Nome do produto")).toHaveCount(0);

  await page.getByRole("button", { name: "Novo produto" }).click();
  await expect(page.getByRole("heading", { name: "Estruture seu próximo produto" })).toBeVisible();
  await page.getByLabel("Nome do produto").fill("Rascunho do tenant A");
  await page.getByRole("article").filter({ hasText: "Draft existente" }).getByRole("link", { name: "Abrir" }).click();
  await expect(page.getByRole("dialog", { name: "Sair desta edição?" })).toBeVisible();
  await page.getByRole("button", { name: "Continuar editando" }).click();
  await expect(page.getByLabel("Nome do produto")).toHaveValue("Rascunho do tenant A");
  await page.getByLabel("Sua loja").selectOption({ label: "Studio B · @studio-b" });
  await expect(page.getByText("Você tem alterações não salvas.", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Sua loja")).toHaveValue(sellerId);
  await expect(page.getByLabel("Nome do produto")).toHaveValue("Rascunho do tenant A");
  await page.getByRole("button", { name: "Descartar e trocar" }).click();
  await expect(page.getByLabel("Sua loja")).toHaveValue(otherSellerId);
  await expect(page.getByLabel("Nome do produto")).toHaveValue("");
  await page.getByLabel("Sua loja").selectOption({ label: "Studio Mobile · @studio-mobile" });

  await page.getByLabel("Nome do produto").focus();
  await page.keyboard.type("Creator OS Mobile");
  await page.getByLabel("Endereço do produto", { exact: false }).fill("creator-os-mobile");
  await page.getByLabel("Descrição curta").fill("Fluxo mobile persistente e privado.");
  await page.getByLabel("Nome do conteúdo").fill("Fundamentos Mobile");
  await page.getByRole("button", { name: "Criar produto" }).click();
  await createStarted;
  await expect(page.getByLabel("Sua loja")).toBeDisabled();
  releaseCreate?.();
  await expect(page.getByRole("heading", { name: "Creator OS Mobile" })).toBeVisible();
  // O conteúdo passou a dividir uma linha com o endereço do produto no cartão
  // ("@endereço · Conteúdo"), então a correspondência exata deixou de valer.
  await expect(page.locator(".draft-card-meta").filter({ hasText: "Fundamentos Mobile" })).toBeVisible();
  await expect(page.getByLabel("Sua loja")).toBeEnabled();
  // Criar fecha o formulário: deixá-lo aberto e vazio convida a criar de novo
  // sem querer.
  await expect(page.getByLabel("Nome do produto")).toHaveCount(0);

  const dimensions = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
    targets: [...document.querySelectorAll("button, input, select, textarea")]
      .map((element) => element.getBoundingClientRect())
      .filter((rect) => rect.width > 0 && rect.height > 0)
      .map((rect) => rect.height)
  }));
  expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client);
  expect(dimensions.targets.every((height) => height >= 44)).toBe(true);

  await page.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
  const scaled = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
    offenders: [...document.querySelectorAll("body *")].flatMap((element) => {
      const rect = element.getBoundingClientRect();
      return rect.right > document.documentElement.clientWidth + 1 || rect.left < -1
        ? [{ tag: element.tagName, className: element.className, text: element.textContent?.trim().slice(0, 80), left: rect.left, right: rect.right, width: rect.width }]
        : [];
    }).slice(0, 10)
  }));
  expect(scaled.scroll, JSON.stringify(scaled.offenders)).toBeLessThanOrEqual(scaled.client);
});

test("preserves loaded products when the next catalog page fails", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "catalog pagination behavior runs once in Chromium");
  const sellerId = "52000000-0000-4000-8000-000000000001";
  const cursor = "53000000-0000-4000-8000-000000000001";
  await page.route("**/api/sellers", async (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ sellers: [{ id: sellerId, slug: "studio-mobile", display_name: "Studio Mobile", role: "seller_owner" }] })
  }));
  await page.route(`**/api/sellers/${sellerId}/products*`, async (route) => {
    const requestUrl = new URL(route.request().url());
    if (requestUrl.searchParams.has("cursor")) {
      expect(requestUrl.searchParams.get("cursor")).toBe(cursor);
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "unavailable" }) });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        products: [{ id: cursor, slug: "pagina-um", title: "Produto da primeira página", description: "Preservado.", status: "draft", created_at: "2026-07-11T11:00:00Z", course: { id: "54000000-0000-4000-8000-000000000001", title: "Curso preservado", status: "draft" } }],
        next_cursor: cursor
      })
    });
  });

  await enterAndWaitForSession(page);
  await page.goto("/products");
  // A single space needs no manual pick: the workspace opens already scoped.
  await expect(page.getByText("@studio-mobile · proprietário")).toBeVisible();
  await expect(page.getByLabel("Sua loja")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Produto da primeira página" })).toBeVisible();
  await page.getByRole("button", { name: "Carregar mais produtos" }).click();
  await expect(page.getByText("Os produtos já carregados foram preservados.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Produto da primeira página" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Tentar carregar novamente" })).toBeVisible();
});

test("edits, previews and publishes a tenant-scoped product at 320px", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "authoring flow runs once in Chromium");
  const sellerId = "62000000-0000-4000-8000-000000000001";
  const productId = "63000000-0000-4000-8000-000000000001";
  const courseId = "64000000-0000-4000-8000-000000000001";
  const offerId = "65000000-0000-4000-8000-000000000001";
  let conflictOnce = true;
  const publishKeys: string[] = [];
  const submittedOfferPrices: string[] = [];
  const state = { title: "Creator OS", slug: "creator-os", description: "Draft privado.", courseTitle: "Fundamentos", offerTitle: "Oferta Creator", offerSummary: "Termos informativos.", offerPrice: null as number | null, productVersion: 1, courseVersion: 1, offerVersion: 1, status: "draft", hasChanges: true, publication: null as null | { id: string; version: number; published_at: string } };
  const detail = () => ({
    product: {
      id: productId, seller_organization_id: sellerId, slug: state.slug, title: state.title, description: state.description, status: state.status,
      version: state.productVersion, draft_version: state.productVersion,
      course: { id: courseId, title: state.courseTitle, description: "", status: "draft", draft_version: state.courseVersion, position: 1 },
      courses: [{ id: courseId, title: state.courseTitle, description: "", status: "draft", draft_version: state.courseVersion, position: 1 }],
      offer: { id: offerId, title: state.offerTitle, summary: state.offerSummary, status: "draft", version: state.offerVersion, draft_version: state.offerVersion, unit_amount_minor: state.offerPrice, currency: "BRL", billing_period: "one_time" },
      publication: state.publication,
      has_unpublished_changes: state.hasChanges,
      readiness: { can_publish: state.hasChanges, items: [{ key: "server", label: state.hasChanges ? "Validação do servidor concluída" : "Nenhuma alteração nova", ready: state.hasChanges }] }
    },
    csrf: { update: "csrf-update", publish: "csrf-publish" }
  });

  await page.route(`**/api/sellers/${sellerId}/products/${productId}`, async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(detail()) });
    const posted = route.request().postDataBuffer();
    const form = await new Request(route.request().url(), { method: "PATCH", headers: route.request().headers(), body: posted ? new Uint8Array(posted) : undefined }).formData();
    const keys = [...form.keys()].sort();
    const productOnlyKeys = ["_csrf", "course_id", "course_title", "expected_course_version", "expected_product_version", "idempotency_key", "product_description", "product_title", "slug"];
    const offerKeys = ["_csrf", "course_id", "course_title", "expected_course_version", "expected_offer_version", "expected_product_version", "idempotency_key", "offer_billing_period", "offer_currency", "offer_id", "offer_summary", "offer_title", "offer_unit_amount_minor", "product_description", "product_title", "slug"];
    expect([productOnlyKeys, offerKeys]).toContainEqual(keys);
    expect(form.get("_csrf")).toBe("csrf-update");
    expect(form.get("course_id")).toBe(courseId);
    if (keys.includes("offer_id")) {
      expect(form.get("offer_id")).toBe(offerId);
      expect(form.get("offer_currency")).toBe("BRL");
      expect(form.get("offer_billing_period")).toBe("one_time");
      submittedOfferPrices.push(String(form.get("offer_unit_amount_minor")));
    }
    if (conflictOnce) {
      conflictOnce = false;
      state.title = "Título concorrente do servidor";
      state.productVersion += 1;
      return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: "version_conflict" }) });
    }
    expect(form.get("expected_product_version")).toBe(String(state.productVersion));
    state.title = String(form.get("product_title")); state.slug = String(form.get("slug")); state.description = String(form.get("product_description")); state.courseTitle = String(form.get("course_title"));
    state.productVersion += 1; state.courseVersion += 1;
    if (keys.includes("offer_id")) {
      state.offerTitle = String(form.get("offer_title")); state.offerSummary = String(form.get("offer_summary")); state.offerPrice = Number(form.get("offer_unit_amount_minor"));
      state.offerVersion += 1;
    }
    state.hasChanges = true;
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ product_id: productId, course_id: courseId, offer_id: offerId, product_version: state.productVersion, course_version: state.courseVersion, offer_version: state.offerVersion }) });
  });
  await page.route(`**/api/sellers/${sellerId}/products/${productId}/preview`, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ preview: true, snapshot: { product: { id: productId, title: state.title, slug: state.slug, description: state.description, status: "draft" }, courses: [{ title: state.courseTitle }], offer: { title: state.offerTitle, summary: state.offerSummary, status: "draft" } }, versions: { product: state.productVersion, course: state.courseVersion, offer: state.offerVersion } }) }));
  // No payout account bound, which is the state that must disable the sale
  // option with a reason the seller can act on.
  await page.route(`**/api/sellers/${sellerId}/payout-account`, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ payout_account: null }) }));
  const accessLinkLabels: string[] = [];
  await page.route(`**/api/sellers/${sellerId}/products/${productId}/access-links`, async (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ access_links: [] }) });
    }
    const posted = route.request().postDataBuffer();
    const form = await new Request(route.request().url(), { method: "POST", headers: route.request().headers(), body: posted ? new Uint8Array(posted) : undefined }).formData();
    accessLinkLabels.push(String(form.get("label")));
    return route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ access_link: { access_link_id: "9f1d6f1e-0e2a-4a3b-8c4d-5e6f70819293", token_prefix: "aabbccdd", label: form.get("label"), max_uses: null, redeemed_count: 0, expires_at: null, created_at: null }, url: `${E2E_BUILD_PUBLIC_ORIGIN}/c/${"a".repeat(64)}` }) });
  });
  await page.route(`**/api/sellers/${sellerId}/products/${productId}/publications`, async (route) => {
    const posted = route.request().postDataBuffer();
    const form = await new Request(route.request().url(), { method: "POST", headers: route.request().headers(), body: posted ? new Uint8Array(posted) : undefined }).formData();
    expect([...form.keys()].sort()).toEqual(["_csrf", "access_mode", "course_versions", "expected_offer_version", "expected_product_version", "idempotency_key", "offer_id"]);
    expect(form.get("_csrf")).toBe("csrf-publish");
    // The access decision travels with the publication. Without a payout account
    // the sale option is refused, so this flow can only ever publish free access.
    expect(form.get("access_mode")).toBe("free");
    expect(form.get("course_versions")).toBe(JSON.stringify([{ id: courseId, version: state.courseVersion }]));
    publishKeys.push(String(form.get("idempotency_key")));
    if (publishKeys.length === 1) return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "upstream_unavailable" }) });
    if (publishKeys.length === 2) expect(publishKeys[1]).toBe(publishKeys[0]);
    if (publishKeys.length === 3) {
      expect(publishKeys[2]).not.toBe(publishKeys[1]);
      return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: "version_conflict" }) });
    }
    if (publishKeys.length === 4) expect(publishKeys[3]).not.toBe(publishKeys[2]);
    const publicationVersion = state.publication ? state.publication.version + 1 : 1;
    state.status = "published"; state.hasChanges = false; state.publication = { id: "66000000-0000-4000-8000-000000000001", version: publicationVersion, published_at: "2026-07-11T15:00:00Z" };
    return route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ publication: state.publication, product_version: state.productVersion }) });
  });

  await page.setViewportSize({ width: 320, height: 568 });
  await enterAndWaitForSession(page);
  await page.goto(`/products/${sellerId}/${productId}`);
  await expect(page.getByRole("heading", { name: "Creator OS", exact: true })).toBeVisible();
  await page.getByLabel("Nome do produto").fill("Creator OS 2");
  await expect(page.getByLabel("Nome do produto")).toBeFocused();
  await page.goBack();
  await expect(page.getByRole("dialog", { name: "Sair desta edição?" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Continuar editando" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(page.getByRole("button", { name: "Confirmar saída" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Continuar editando" })).toBeFocused();
  await page.getByRole("button", { name: "Continuar editando" }).click();
  await expect(page).toHaveURL(`/products/${sellerId}/${productId}`);
  await expect(page.getByLabel("Nome do produto")).toBeFocused();
  await expect(page.getByLabel("Nome do produto")).toHaveValue("Creator OS 2");
  await page.goBack();
  await expect(page.getByRole("dialog", { name: "Sair desta edição?" })).toBeVisible();
  await page.getByRole("button", { name: "Continuar editando" }).click();
  await expect(page.getByLabel("Nome do produto")).toBeFocused();
  const libraryLink = page.getByRole("navigation", { name: "Navegação móvel" }).getByRole("link", { name: "Minha biblioteca" });
  await libraryLink.click();
  await expect(page.getByRole("dialog", { name: "Sair desta edição?" })).toBeVisible();
  await page.getByRole("button", { name: "Continuar editando" }).click();
  await expect(libraryLink).toBeFocused();
  await expect(page.getByLabel("Nome do produto")).toHaveValue("Creator OS 2");
  await page.getByRole("button", { name: "Salvar rascunho" }).click();
  await expect(page.getByRole("button", { name: "Recarregar versão do servidor" })).toBeVisible();
  await page.getByRole("button", { name: "Recarregar versão do servidor" }).click();
  await expect(page.getByText("Versão atualizada; suas alterações locais continuam prontas para novo envio.")).toBeVisible();
  await expect(page.getByLabel("Nome do produto")).toHaveValue("Creator OS 2");
  await page.getByRole("button", { name: "Salvar rascunho" }).click();
  await expect(page.getByText("Tudo salvo com segurança.")).toBeVisible();

  await page.getByRole("link", { name: /^Preço/ }).click();
  const offerPrice = page.getByLabel("Preço (R$)");
  await offerPrice.fill("47");
  await expect(page.getByText("Salve o preço antes de ativar vendas", { exact: true })).toBeVisible();
  const offerSummary = page.getByLabel("Descrição no checkout");
  await offerSummary.fill("Oferta sem cobrança conectada.");
  await offerSummary.focus();
  await offerSummary.evaluate((element) => (element as HTMLTextAreaElement).form?.requestSubmit());
  await expect(page.getByText("Tudo salvo com segurança.")).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/products/${sellerId}/${productId}\\?section=oferta$`));
  await expect(offerSummary).toBeFocused();
  await page.goBack();
  await expect(page).toHaveURL("/dashboard");
  await page.goForward();
  await expect(page).toHaveURL(new RegExp(`/products/${sellerId}/${productId}\\?section=oferta$`));
  await expect(page.getByLabel("Descrição no checkout")).toHaveValue("Oferta sem cobrança conectada.");
  await expect(page.getByLabel("Preço (R$)")).toHaveValue("47");
  await expect(page.getByText("Preço salvo; catálogo pronto para publicar", { exact: true })).toBeVisible();
  await expect(page.getByText("A alteração do acesso é feita na etapa Distribuição, depois que o rascunho estiver salvo.", { exact: true })).toBeVisible();
  // A prévia deixou de ser etapa de navegação na Onda 3 e virou "Ver como
  // aluno", alcançável de qualquer área. O que se afirma continua igual: o
  // vendedor vê o produto como o aluno veria, e a compra não existe ali.
  await page.getByRole("button", { name: "Ver como aluno" }).click();
  const studentPreview = page.getByRole("dialog");
  await expect(studentPreview.getByRole("heading", { name: "Creator OS 2" })).toBeVisible();
  await expect(studentPreview.getByText("Compra indisponível nesta prévia")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(studentPreview).toBeHidden();
  await page.getByRole("navigation", { name: "Áreas do produto" })
    .getByRole("link", { name: /^Distribuição/ }).click();
  await page.getByRole("button", { name: "Publicar catálogo primeira versão" }).click();
  await expect(page.getByRole("dialog", { name: "Colocar Creator OS 2 no ar?" })).toBeVisible();
  // Publishing now decides access. Selling stays visible but refused, with the
  // reason written next to it, instead of leaving a dead button on the screen.
  await expect(page.getByRole("radio", { name: /Acesso gratuito/ })).toBeChecked();
  await expect(page.getByRole("radio", { name: /Vender com checkout/ })).toBeDisabled();
  await expect(page.getByText("configure seus recebimentos", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Revisar novamente" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Colocar no ar" })).toBeFocused();
  await page.getByRole("button", { name: "Colocar no ar" }).click();
  await expect(page.getByText("A API não respondeu.", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Publicar catálogo primeira versão" }).click();
  await page.getByRole("button", { name: "Colocar no ar" }).click();
  await expect(page.getByText("Catálogo publicado com sucesso.", { exact: false })).toBeVisible();
  await expect(page.getByText("Nenhuma mudança desde a versão publicada.", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: /Publicar catálogo nova versão/ })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Ativar vendas e usar checkout" })).toBeEnabled();
  await page.getByRole("navigation", { name: "Áreas do produto" }).getByRole("link", { name: /^Produto/ }).click();
  await page.getByLabel("Descrição curta").fill("Novo draft depois do snapshot.");
  await page.getByRole("button", { name: "Salvar rascunho" }).click();
  await expect(page.getByText("Tudo salvo com segurança.")).toBeVisible();
  await page.getByRole("link", { name: /^Distribuição/ }).click();
  await expect(page.getByRole("button", { name: "Publicar catálogo nova versão após v1" })).toBeEnabled();
  await page.getByRole("button", { name: "Publicar catálogo nova versão após v1" }).click();
  await page.getByRole("button", { name: "Colocar no ar" }).click();
  await expect(page.getByText("O produto mudou ou não está pronto.", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Publicar catálogo nova versão após v1" }).click();
  await page.getByRole("button", { name: "Colocar no ar" }).click();
  await expect(page.getByText("Catálogo publicado com sucesso.", { exact: false })).toBeVisible();
  // The link fields live inside the editor's own form. Pressing Enter there used
  // to submit the product draft instead of creating the link.
  await page.getByLabel("Nome da turma").fill("Turma piloto");
  await page.getByLabel("Nome da turma").press("Enter");
  await expect(page.getByText("Copie agora", { exact: false })).toBeVisible();
  expect(accessLinkLabels).toEqual(["Turma piloto"]);
  expect(publishKeys).toHaveLength(4);
  expect(submittedOfferPrices).toContain("4700");

  await page.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
  const scaled = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
    scrollX: window.scrollX,
    active: document.activeElement instanceof HTMLElement ? { tag: document.activeElement.tagName, text: (document.activeElement.textContent?.trim() || document.activeElement.getAttribute("name"))?.slice(0, 80) } : null,
    clipped: [...document.querySelectorAll("a,button,input,select,textarea")].flatMap((element) => {
      const rect = element.getBoundingClientRect();
      if (rect.width <= 0 || (rect.left >= -1 && rect.right <= document.documentElement.clientWidth + 1)) return [];
      const parentRect = element.parentElement?.getBoundingClientRect();
      const style = getComputedStyle(element);
      return [{
        text: element.textContent?.trim() || element.getAttribute("name"),
        rect: { left: rect.left, right: rect.right, width: rect.width },
        parent: parentRect ? { left: parentRect.left, right: parentRect.right, width: parentRect.width } : null,
        style: { display: style.display, width: style.width, minWidth: style.minWidth, maxWidth: style.maxWidth, paddingInline: style.paddingInline, justifySelf: style.justifySelf }
      }];
    })
  }));
  expect(scaled.scroll).toBeLessThanOrEqual(scaled.client);
  expect(scaled.clipped, JSON.stringify(scaled)).toEqual([]);
  await page.evaluate(() => { document.documentElement.style.fontSize = "100%"; });
  await page.setViewportSize({ width: 568, height: 320 });
  const landscape = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
  expect(landscape.scroll).toBeLessThanOrEqual(landscape.client);
});

test("authors and reorders a tenant curriculum at 320px without drag", async ({ page }) => {
  const sellerId = "82000000-0000-4000-8000-000000000001";
  const productId = "83000000-0000-4000-8000-000000000001";
  const courseId = "84000000-0000-4000-8000-000000000001";
  const baseModuleId = "85000000-0000-4000-8000-000000000001";
  let version = 3;
  let productArchived = false;
  const moduleAttemptKeys: string[] = [];
  let practiceArchived = false;
  const modules = [{
    id: baseModuleId,
    title: "Base",
    status: "active",
    position: 0,
    lessons: [{
      id: "86000000-0000-4000-8000-000000000001",
      title: "Boas-vindas",
      kind: "text",
      content: "Comece por aqui.",
      status: "active",
      position: 0
    }]
  }];

  await page.setViewportSize({ width: 320, height: 800 });
  await enterAndWaitForSession(page);
  await page.route("**/api/sellers/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const productPath = `/api/sellers/${sellerId}/products/${productId}`;
    const curriculumPath = `${productPath}/courses/${courseId}`;
    if (path === productPath && request.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        product: {
          id: productId,
          seller_organization_id: sellerId,
          slug: "curso-mobile",
          title: "Curso Mobile",
          description: "Editor de currículo.",
          status: productArchived ? "archived" : "draft",
          draft_version: 1,
          course: { id: courseId, title: "Jornada Mobile", status: "draft", draft_version: version },
          courses: [{ id: courseId, title: "Jornada Mobile", status: "draft", draft_version: version }],
          offer: { id: null, title: "", summary: "", status: "draft", draft_version: 0 },
          publication: null,
          has_unpublished_changes: true,
          readiness: { can_publish: false, items: [{ key: "offer_required", label: "Crie a oferta.", ready: false }] }
        },
        csrf: { update: "update-token", publish: "publish-token" }
      }) });
      return;
    }
    if (path === `${curriculumPath}/curriculum` && request.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        course: { id: courseId, draft_version: version },
        modules,
        csrf: { create: "create-token", update: "update-token", reorder: "reorder-token", archive: "archive-token" },
        limits: { modules_per_course: 50, lessons_per_module: 100, lessons_per_course: 500, title_bytes: 160, lesson_content_bytes: 8192 }
      }) });
      return;
    }
    // O painel de material complementar carrega a lista ao montar. Ele não é o
    // objeto deste teste, mas cai neste mesmo manipulador, e sem uma resposta
    // aqui a requisição seguia para o leitor de formulário e morria com um erro
    // sobre content-type que não dizia nada sobre a causa.
    if (path.endsWith("/materials") && request.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        materials: [],
        csrf: { create: "create-token", update: "update-token", reorder: "reorder-token", archive: "archive-token" }
      }) });
      return;
    }
    // A lista do curso pede os arquivos de cada aula para mostrar o estado
    // dela (#649), e a aula aberta pede os mesmos arquivos para o painel.
    if (path.endsWith("/assets") && request.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        assets: [],
        uploads: [],
        csrf: { intent: "i", confirm: "c", cancel: "x", archive: "a", policy: "p", signPart: "s" }
      }) });
      return;
    }
    // Daqui para baixo só passa envio de formulário. Qualquer outra coisa é uma
    // rota que a tela passou a chamar e este teste ainda não conhece: falhar
    // dizendo isso custa menos do que um erro de content-type.
    if (request.method() === "GET") {
      throw new Error(`requisição não prevista por este teste: ${request.method()} ${path}`);
    }
    const posted = request.postDataBuffer();
    const form = await new Request(request.url(), {
      method: request.method(),
      headers: request.headers(),
      body: posted ? new Uint8Array(posted) : undefined
    }).formData();
    expect(form.get("idempotency_key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(Number(form.get("expected_course_version"))).toBe(version);
    if (path === `${curriculumPath}/modules` && request.method() === "POST") {
      expect(form.get("_csrf")).toBe("create-token");
      moduleAttemptKeys.push(String(form.get("idempotency_key")));
      if (moduleAttemptKeys.length === 1) {
        await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "temporarily_unavailable" }) });
        return;
      }
      version += 1;
      const id = "85000000-0000-4000-8000-000000000002";
      modules.push({ id, title: String(form.get("title")), status: "active", position: 1, lessons: [] });
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ module_id: id, course_version: version, created: true }) });
      return;
    }
    if (path === `${curriculumPath}/modules/${baseModuleId}/lessons` && request.method() === "POST") {
      expect(form.get("kind")).toBe("text");
      version += 1;
      const id = "86000000-0000-4000-8000-000000000002";
      modules[0].lessons.push({ id, title: String(form.get("title")), kind: "text", content: String(form.get("content")), status: "active", position: 1 });
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ lesson_id: id, course_version: version, created: true }) });
      return;
    }
    if (path === `${curriculumPath}/modules/${baseModuleId}/lessons/order` && request.method() === "PUT") {
      const order = JSON.parse(String(form.get("ordered_ids"))) as string[];
      expect(order).toEqual(["86000000-0000-4000-8000-000000000002", "86000000-0000-4000-8000-000000000001"]);
      modules[0].lessons.sort((left, right) => order.indexOf(left.id) - order.indexOf(right.id));
      version += 1;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ course_version: version, reordered: true }) });
      return;
    }
    if (path === `${curriculumPath}/modules/${baseModuleId}/lessons/86000000-0000-4000-8000-000000000002` && request.method() === "PATCH") {
      expect(form.get("_csrf")).toBe("update-token");
      expect(form.get("kind")).toBe("text");
      const lesson = modules[0].lessons.find((entry) => entry.id === "86000000-0000-4000-8000-000000000002")!;
      lesson.title = String(form.get("title"));
      lesson.content = String(form.get("content"));
      version += 1;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ course_version: version, updated: true }) });
      return;
    }
    if (path === `${curriculumPath}/modules/${baseModuleId}/lessons/86000000-0000-4000-8000-000000000002/archive` && request.method() === "POST") {
      expect(form.get("_csrf")).toBe("archive-token");
      modules[0].lessons = modules[0].lessons.filter((lesson) => lesson.id !== "86000000-0000-4000-8000-000000000002");
      practiceArchived = true;
      version += 1;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ course_version: version, archived: true }) });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: "unexpected_test_route" }) });
  });

  await page.goto(`/products/${sellerId}/${productId}?section=curriculo`);
  await expect(page.getByRole("heading", { name: "Seu curso" })).toBeVisible();
  await page.getByRole("button", { name: "+ Novo módulo" }).click();
  await page.getByLabel("Nome do módulo").fill("Avançado");
  await page.getByRole("button", { name: "Criar módulo" }).click();
  // A falha diz o que fazer, e o nome digitado continua no campo.
  await expect(page.getByTestId("curriculum-live")).toContainText("Confira a internet e tente de novo.");
  await expect(page.getByLabel("Nome do módulo")).toHaveValue("Avançado");
  await page.getByRole("button", { name: "Criar módulo" }).click();
  await expect(page.getByRole("heading", { name: "Avançado" })).toBeVisible();
  expect(moduleAttemptKeys).toHaveLength(2);
  expect(moduleAttemptKeys[1]).toBe(moduleAttemptKeys[0]);
  // O módulo novo já oferece o nome da primeira aula; aqui a aula vai no outro.
  await page.getByRole("button", { name: "Cancelar" }).click();

  await page.getByRole("button", { name: "Nova aula em Base" }).click();
  await page.getByLabel("Nome da nova aula").fill("Prática guiada");
  await page.getByRole("button", { name: "Criar e abrir a aula" }).click();
  await expect(page.getByRole("heading", { level: 2, name: /Prática guiada/ })).toBeVisible();
  await page.getByRole("textbox", { name: "Texto da aula" }).fill("Faça o exercício com calma e registre o resultado.");
  await expect(page.getByText("✓ Salvo")).toBeVisible();
  await page.getByRole("button", { name: "Voltar para o curso", exact: true }).click();

  const moveUp = page.getByRole("button", { name: "Subir aula Prática guiada" });
  await moveUp.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Descer aula Prática guiada" })).toBeFocused();
  await expect(page.getByTestId("curriculum-live")).toHaveText("Aula Prática guiada agora é a 1ª de 2.");

  // Excluir não pergunta nada: a aula sai na hora, o aviso oferece Desfazer,
  // e o foco vai para a aula que ficou, e não para o body (#649).
  await page.getByTestId("lesson-86000000-0000-4000-8000-000000000002").getByRole("link").click();
  await page.getByRole("button", { name: "Excluir aula" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator(".undo-toast")).toContainText("Aula “Prática guiada” excluída.");
  await expect(page.getByTestId("lesson-86000000-0000-4000-8000-000000000002")).toHaveCount(0);
  await expect(page.locator('[data-lesson-link="86000000-0000-4000-8000-000000000001"]')).toBeFocused();
  // Sem desfazer, o pedido sai quando o aviso vence.
  await expect.poll(() => practiceArchived, { timeout: 15_000 }).toBe(true);
  await expect(page.locator(".undo-toast")).toHaveCount(0);

  const touchAndWidth = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
    shortTargets: [...document.querySelectorAll("button,input,textarea,summary")]
      .filter((element) => {
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && rect.height < 44;
      }).length
  }));
  expect(touchAndWidth.scroll).toBeLessThanOrEqual(touchAndWidth.client);
  expect(touchAndWidth.shortTargets).toBe(0);
  await page.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
  const scaled = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
    moduleIntrinsicInlineSize: getComputedStyle(document.querySelector(".course-module")!).containIntrinsicInlineSize,
    clipped: [...document.querySelectorAll("a,button,input,textarea,summary")].flatMap((element) => {
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && (rect.left < -1 || rect.right > document.documentElement.clientWidth + 1)
        ? [{ text: element.textContent?.trim() || element.getAttribute("name"), left: rect.left, right: rect.right }]
        : [];
    })
  }));
  expect(scaled.scroll).toBeLessThanOrEqual(scaled.client);
  expect(scaled.moduleIntrinsicInlineSize).not.toContain("520px");
  expect(scaled.clipped, JSON.stringify(scaled)).toEqual([]);
  await page.evaluate(() => { document.documentElement.style.fontSize = "100%"; });
  await page.setViewportSize({ width: 568, height: 320 });
  const landscape = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
  expect(landscape.scroll).toBeLessThanOrEqual(landscape.client);

  productArchived = true;
  await page.reload();
  // Produto encerrado: dá para ver o curso inteiro, mas nada para mudar.
  await expect(page.getByText("Este produto foi encerrado. Dá para ver o curso, mas não para mudar.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Avançado" })).toBeVisible();
  await expect(page.getByRole("button", { name: "+ Novo módulo" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^(Subir|Descer) / })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Nova aula em / })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Mudar nome do módulo / })).toHaveCount(0);
});

test("keeps the PDF uploader accessible at mobile widths and 200% text zoom", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "asset upload interaction runs once in Chromium");
  const sellerId = "92000000-0000-4000-8000-000000000001";
  const productId = "93000000-0000-4000-8000-000000000001";
  const courseId = "94000000-0000-4000-8000-000000000001";
  const moduleId = "95000000-0000-4000-8000-000000000001";
  const lessonId = "96000000-0000-4000-8000-000000000001";
  const assetId = "97000000-0000-4000-8000-000000000001";
  const secondAssetId = "97000000-0000-4000-8000-000000000002";
  const uploadId = "98000000-0000-4000-8000-000000000001";
  const productPath = `/api/sellers/${sellerId}/products/${productId}`;
  const curriculumPath = `${productPath}/courses/${courseId}`;
  const assetsPath = `${curriculumPath}/modules/${moduleId}/lessons/${lessonId}/assets`;
  let intentAttempts = 0;
  const intentReplacementTargets: Array<string | null> = [];
  const policyWrites: Array<{ contentRole: string; downloadAllowed: string }> = [];
  let assetContentRole: "course_content" | "support_material" = "course_content";
  let assetDownloadAllowed = false;
  let courseVersion = 4;
  let releaseUpload!: () => void;
  let markUploadStarted!: () => void;
  let markCancelCalled!: () => void;
  let markPolicyStarted!: () => void;
  let releasePolicy!: () => void;
  let markRejectedPolicyStarted!: () => void;
  let releaseRejectedPolicy!: () => void;
  let rejectNextPolicy = false;
  let rejectedPolicyWrite = false;
  const uploadStarted = new Promise<void>((resolve) => { markUploadStarted = resolve; });
  const cancelCalled = new Promise<void>((resolve) => { markCancelCalled = resolve; });
  const policyStarted = new Promise<void>((resolve) => { markPolicyStarted = resolve; });
  const rejectedPolicyStarted = new Promise<void>((resolve) => { markRejectedPolicyStarted = resolve; });

  await page.setViewportSize({ width: 360, height: 844 });
  await enterAndWaitForSession(page);
  await page.route(`${E2E_STORAGE_ORIGIN}/**`, async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({
        status: 204,
        headers: {
          "access-control-allow-origin": E2E_BUILD_PUBLIC_ORIGIN,
          "access-control-allow-methods": "PUT,OPTIONS",
          "access-control-allow-headers": "content-type,x-amz-checksum-sha256,x-amz-meta-appsell-upload-id"
        }
      });
      return;
    }
    markUploadStarted();
    await new Promise<void>((resolve) => { releaseUpload = resolve; });
    await route.fulfill({ status: 200, headers: { "access-control-allow-origin": E2E_BUILD_PUBLIC_ORIGIN }, body: "" });
  });
  await page.route("**/api/sellers/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === productPath && request.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        product: {
          id: productId,
          seller_organization_id: sellerId,
          slug: "pdf-mobile",
          title: "PDF Mobile",
          description: "Editor de material privado.",
          status: "draft",
          draft_version: 2,
          course: { id: courseId, title: "Curso PDF", status: "draft", draft_version: courseVersion },
          courses: [{ id: courseId, title: "Curso PDF", status: "draft", draft_version: courseVersion }],
          offer: { id: null, title: "", summary: "", status: "draft", draft_version: 0 },
          publication: null,
          has_unpublished_changes: true,
          readiness: { can_publish: false, items: [{ key: "offer_required", label: "Crie a oferta.", ready: false }] }
        },
        csrf: { update: "update-token", publish: "publish-token" }
      }) });
      return;
    }
    if (path === `${curriculumPath}/curriculum` && request.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        course: { id: courseId, draft_version: courseVersion },
        modules: [{
          id: moduleId,
          title: "Base PDF",
          status: "active",
          position: 0,
          lessons: [{ id: lessonId, title: "Aula PDF", kind: "text", content: "Material complementar.", status: "active", position: 0 }]
        }],
        csrf: { create: "create-token", update: "update-token", reorder: "reorder-token", archive: "archive-token" },
        limits: { modules_per_course: 50, lessons_per_module: 100, lessons_per_course: 500, title_bytes: 160, lesson_content_bytes: 8192 }
      }) });
      return;
    }
    if (path === assetsPath && request.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        assets: [
          { id: assetId, title: "Guia atual", file_name: "guia-atual.pdf", media_type: "application/pdf", byte_size: 3181, status: "ready", content_role: assetContentRole, download_allowed: assetDownloadAllowed, safe_reason: null },
          { id: secondAssetId, title: "Exercícios", file_name: "exercicios.pdf", media_type: "application/pdf", byte_size: 4096, status: "ready", content_role: "course_content", download_allowed: false, safe_reason: null }
        ],
        uploads: [],
        csrf: { intent: "intent-token", confirm: "confirm-token", cancel: "cancel-token", archive: "asset-archive-token", policy: "asset-policy-token" }
      }) });
      return;
    }
    // Os links da aula moram em "Materiais para o aluno", à vista desde o #649.
    if (path.endsWith(`/lessons/${lessonId}/materials`) && request.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        materials: [],
        csrf: { create: "material-create-token", archive: "material-archive-token" }
      }) });
      return;
    }
    if (path === `${assetsPath}/upload-intents` && request.method() === "POST") {
      const posted = request.postDataBuffer();
      const form = await new Request(request.url(), {
        method: "POST",
        headers: request.headers(),
        body: posted ? new Uint8Array(posted) : undefined
      }).formData();
      expect(form.get("title")).toBe("Aula PDF");
      expect(form.get("file_name")).toBe("guia-novo.pdf");
      intentReplacementTargets.push(form.get("existing_asset_id")?.toString() ?? null);
      intentAttempts += 1;
      if (intentAttempts === 1 || intentAttempts === 3 || intentAttempts === 4) {
        await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "temporarily_unavailable" }) });
        return;
      }
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({
        upload_id: uploadId,
        asset_id: "99000000-0000-4000-8000-000000000001",
        upload_url: `${E2E_STORAGE_ORIGIN}/appsell-quarantine/${uploadId}?X-Amz-Signature=test`,
        upload_origin: E2E_STORAGE_ORIGIN,
        expires_at: new Date(Date.now() + 300_000).toISOString(),
        required_headers: {
          "Content-Type": "application/pdf",
          "x-amz-checksum-sha256": "test-checksum",
          "x-amz-meta-appsell-upload-id": uploadId
        }
      }) });
      return;
    }
    if (path === `${assetsPath}/${assetId}/policy` && request.method() === "PATCH") {
      const posted = request.postDataBuffer();
      const form = await new Request(request.url(), {
        method: "PATCH",
        headers: request.headers(),
        body: posted ? new Uint8Array(posted) : undefined
      }).formData();
      expect(form.get("_csrf")).toBe("asset-policy-token");
      if (rejectNextPolicy) {
        rejectNextPolicy = false;
        expect(form.get("content_role")).toBe("support_material");
        expect(form.get("download_allowed")).toBe("true");
        markRejectedPolicyStarted();
        await new Promise<void>((resolve) => { releaseRejectedPolicy = resolve; });
        rejectedPolicyWrite = true;
        await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "policy_unavailable" }) });
        return;
      }
      policyWrites.push({
        contentRole: String(form.get("content_role")),
        downloadAllowed: String(form.get("download_allowed"))
      });
      if (policyWrites.length === 1) {
        markPolicyStarted();
        await new Promise<void>((resolve) => { releasePolicy = resolve; });
      }
      assetContentRole = String(form.get("content_role")) as typeof assetContentRole;
      assetDownloadAllowed = form.get("download_allowed") === "true";
      courseVersion += 1;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        asset_id: assetId,
        content_role: assetContentRole,
        download_allowed: assetDownloadAllowed,
        course_version: courseVersion,
        applied: true,
        replayed: false
      }) });
      return;
    }
    if (path === `${assetsPath}/uploads/${uploadId}/cancel` && request.method() === "POST") {
      const posted = request.postDataBuffer();
      const form = await new Request(request.url(), {
        method: "POST",
        headers: request.headers(),
        body: posted ? new Uint8Array(posted) : undefined
      }).formData();
      expect(form.get("_csrf")).toBe("cancel-token");
      markCancelCalled();
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ upload_id: uploadId, status: "cancelled" }) });
      return;
    }
    if (path === `${assetsPath}/${assetId}/archive` && request.method() === "POST") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ asset_id: assetId, archived: true, course_version: 5 }) });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: "unexpected_test_route" }) });
  });

  const lessonUrl = `/products/${sellerId}/${productId}?section=curriculo&aula=${lessonId}`;
  await page.goto(lessonUrl);
  // Desde o #649 a aula abre em tela própria, com os dois lugares à vista:
  // "Vídeo ou PDF da aula" e "Materiais para o aluno". O uploader coberto por
  // este teste é o do primeiro.
  const lesson = page.locator(".lesson-editor:not([hidden])");
  const conteudo = page.getByRole("region", { name: "Vídeo ou PDF da aula" });
  const anexos = page.getByRole("region", { name: "Materiais para o aluno" });
  await expect(conteudo.locator(".lesson-file")).toHaveCount(2);
  // O nome do arquivo aparece no card, no editor de quem vende, que foi quem
  // escolheu o arquivo. A P1.4 de 23/08/2026 proíbe o nome físico chegar ao
  // **aluno**, e o `title` (que viaja para lá) segue sendo o texto da aula.
  await expect(conteudo.locator(".lesson-file").first()).toContainText("guia-atual.pdf");
  const input = conteudo.locator('input[type="file"]');
  await expect(input).toHaveAccessibleName("+ Adicionar outro vídeo ou PDF");
  const feedback = conteudo.locator(".lesson-upload-state");
  await expect(feedback).toHaveAttribute("role", "status");
  await expect(feedback).toHaveAttribute("aria-live", "polite");
  await expect(feedback).toHaveAttribute("aria-atomic", "true");

  // "Uso deste material" era um `<select>` em cada card. Ele saiu: o lugar em
  // que se clica já é a resposta. O PDF é o único formato que cabe nos dois,
  // então ele ganhou uma ação explícita, e é ela que dispara a mesma
  // requisição de política que este teste acompanha.
  await conteudo.getByRole("button", { name: "Mover para Materiais para o aluno" }).first().click();
  await policyStarted;
  await expect(page.getByRole("button", { name: "Parar envio" })).toHaveCount(0);
  const pendingPolicyUrl = page.url();
  await page.getByRole("navigation", { name: "Navegação móvel" })
    .getByRole("link", { name: "Minha biblioteca" }).click();
  await expect(page.getByRole("dialog", { name: "Sair desta edição?" })).toBeVisible();
  await page.getByRole("button", { name: "Confirmar saída" }).click();
  await expect(page.getByRole("button", { name: "Preparando saída segura…" })).toBeVisible();
  await expect(page).toHaveURL(pendingPolicyUrl);
  releasePolicy();
  await expect(page).toHaveURL("/library");
  await page.goto(lessonUrl);
  await expect.poll(() => policyWrites).toEqual([
    { contentRole: "support_material", downloadAllowed: "false" }
  ]);
  // O PDF foi movido: quem o mostra agora é "Materiais para o aluno", e é lá
  // que mora a escolha de deixar baixar.
  const downloadToggle = anexos.getByRole("switch", { name: "O aluno pode baixar: guia-atual.pdf" });
  await expect(downloadToggle).toBeVisible();
  await expect(downloadToggle).not.toBeChecked();
  rejectNextPolicy = true;
  await downloadToggle.click({ noWaitAfter: true });
  await rejectedPolicyStarted;
  await page.getByRole("navigation", { name: "Navegação móvel" })
    .getByRole("link", { name: "Minha biblioteca" }).click();
  await page.getByRole("button", { name: "Confirmar saída" }).click();
  await expect(page.getByRole("button", { name: "Preparando saída segura…" })).toBeVisible();
  releaseRejectedPolicy();
  await expect(page).toHaveURL(pendingPolicyUrl);
  await expect(page.getByRole("dialog", { name: "Sair desta edição?" }).getByRole("alert"))
    .toContainText("Não foi possível concluir a operação com segurança");
  expect(rejectedPolicyWrite).toBe(true);
  await page.getByRole("button", { name: "Continuar editando" }).click();
  await expect(downloadToggle).toBeVisible();
  await downloadToggle.click({ noWaitAfter: true });
  await expect.poll(() => policyWrites).toEqual([
    { contentRole: "support_material", downloadAllowed: "false" },
    { contentRole: "support_material", downloadAllowed: "true" }
  ]);
  await expect(downloadToggle).toBeChecked();

  const pdf = { name: "guia-novo.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF") };
  await input.setInputFiles(pdf);
  // O servidor não respondeu: a mensagem diz o que fazer, sem culpar o arquivo.
  await expect(feedback).toContainText("Não deu para preparar o envio.");
  await expect(feedback).toContainText("Confira a internet e escolha o arquivo de novo.");
  await expect(feedback).not.toContainText("guia-novo.pdf");
  await expect(input).toHaveAccessibleName("Escolher o arquivo de novo");
  await input.focus();
  await expect(input).toBeFocused();
  await input.setInputFiles(pdf);
  await uploadStarted;
  await expect(feedback).toContainText(/Enviando \d+%/);
  await expect(conteudo.getByRole("progressbar", { name: "Andamento do envio" })).toBeVisible();
  await conteudo.getByRole("button", { name: "Parar envio" }).click();
  await cancelCalled;
  releaseUpload();
  await expect(feedback).toContainText("Envio parado. Nada mudou na aula.");
  await expect(conteudo.getByRole("button", { name: "Parar envio" })).toHaveCount(0);

  for (const width of [320, 360, 390]) {
    await page.setViewportSize({ width, height: 844 });
    const layout = await lesson.evaluate((element) => ({
      client: document.documentElement.clientWidth,
      scroll: document.documentElement.scrollWidth,
      shortTargets: [...element.querySelectorAll("button,input,select,summary")].flatMap((target) => {
        const rect = target.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && (rect.width < 44 || rect.height < 44)
          ? [{ name: target.textContent?.trim() || target.getAttribute("aria-label") || target.getAttribute("id"), width: rect.width, height: rect.height }]
          : [];
      })
    }));
    expect(layout.scroll, `overflow at ${width}px`).toBeLessThanOrEqual(layout.client);
    expect(layout.shortTargets, `short targets at ${width}px`).toEqual([]);
  }

  await page.setViewportSize({ width: 320, height: 844 });
  await page.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
  const zoomed = await lesson.evaluate((element) => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
    clipped: [...element.querySelectorAll("button,input,select,summary,.lesson-upload-state")].flatMap((target) => {
      const rect = target.getBoundingClientRect();
      return rect.width > 0 && (rect.left < -1 || rect.right > document.documentElement.clientWidth + 1)
        ? [{ name: target.textContent?.trim() || target.getAttribute("id"), left: rect.left, right: rect.right }]
        : [];
    })
  }));
  expect(zoomed.scroll).toBeLessThanOrEqual(zoomed.client);
  expect(zoomed.clipped).toEqual([]);
  await page.evaluate(() => { document.documentElement.style.fontSize = "100%"; });

  // "Vídeo ou PDF da aula" ficou com o segundo PDF; o primeiro foi movido no
  // início deste teste. Tirar da aula não pergunta nada: some na hora, e o
  // aviso oferece Desfazer (#649).
  const segundo = conteudo.getByRole("article", { name: "exercicios.pdf" });
  await segundo.getByRole("button", { name: "Tirar da aula: exercicios.pdf" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(segundo).toHaveCount(0);
  await page.locator(".undo-toast").getByRole("button", { name: "Desfazer" }).click();
  await expect(segundo).toBeVisible();

  // Mirar pelo painel, e não pela ordem no DOM: com dois painéis, `.nth(1)`
  // pularia para o card do outro e o teste mediria a troca errada.
  const substituir = segundo.getByRole("button", { name: "Trocar arquivo: exercicios.pdf" });
  const cancelledChooser = page.waitForEvent("filechooser");
  await substituir.click();
  await (await cancelledChooser).setFiles([]);
  const directChooser = page.waitForEvent("filechooser");
  await input.click();
  await (await directChooser).setFiles(pdf);
  await expect(feedback).toContainText("Não deu para preparar o envio.");
  const replacementChooser = page.waitForEvent("filechooser");
  await substituir.click();
  await (await replacementChooser).setFiles(pdf);
  await expect(feedback).toContainText("Não deu para preparar o envio.");
  expect(intentReplacementTargets).toEqual([null, null, null, secondAssetId]);
});

test("fails publication closed for string readiness and a missing offer", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "blocked readiness runs once in Chromium");
  const sellerId = "72000000-0000-4000-8000-000000000001";
  const productId = "73000000-0000-4000-8000-000000000001";
  const course = { id: "74000000-0000-4000-8000-000000000001", title: "Curso privado", description: "", status: "draft", draft_version: 1, position: 1 };
  await page.route(`**/api/sellers/${sellerId}/products/${productId}`, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ product: { id: productId, seller_organization_id: sellerId, slug: "produto-sem-oferta", title: "Produto sem oferta", description: "Privado", status: "draft", version: 1, draft_version: 1, course, courses: [course], offer: null, publication: null, has_unpublished_changes: true, readiness: { can_publish: false, items: ["offer_missing"] } }, csrf: { update: "csrf-update", publish: "csrf-publish" } }) }));
  await page.route(`**/api/sellers/${sellerId}/products/${productId}/preview`, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ snapshot: { product: { id: productId, title: "Produto sem oferta", slug: "produto-sem-oferta", description: "Privado", status: "draft" }, courses: [{ title: course.title }], offer: null }, versions: { product: 1, course: 1, offer: 0 } }) }));
  await enterAndWaitForSession(page);
  // A prévia deixou de ser uma etapa com endereço próprio na Onda 3: virou o
  // diálogo "Ver como aluno", alcançável de qualquer área. O que este teste
  // afirma continua sendo o mesmo — sem oferta, a prévia diz isso e a
  // publicação permanece fechada.
  await page.goto(`/products/${sellerId}/${productId}?section=produto`);
  await page.getByRole("button", { name: "Ver como aluno" }).click();
  const preview = page.getByRole("dialog");
  await expect(preview.getByText("Preço ainda não configurado")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(preview).toBeHidden();

  await page.getByRole("navigation", { name: "Áreas do produto" })
    .getByRole("link", { name: /^Distribuição/ }).click();
  // No build de produção o painel "O que falta para publicar" já está na tela
  // junto com o de publicar, e os dois listam a pendência (#585). O que este
  // teste afirma é o bloqueio da publicação, então olha o painel dela.
  await expect(page.getByLabel("Publicar catálogo").getByText("offer_missing")).toBeVisible();
  await expect(page.getByRole("button", { name: "Publicar catálogo primeira versão" })).toBeDisabled();
});

test("covers the approved responsive widths", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "viewport matrix runs once in Chromium");
  await enterAndWaitForSession(page);
  for (const width of [320, 360, 390, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    for (const path of ["/", "/dashboard", "/products", "/library"]) {
      await page.goto(path);
      const dimensions = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
      expect(dimensions.scroll, `overflow at ${width}px on ${path}`).toBeLessThanOrEqual(dimensions.client);
      const undersized = await page.locator(".landing a:visible, .landing button:visible, .app-shell a:visible, .app-shell button:visible").evaluateAll((elements) => elements.flatMap((element) => {
        const rect = element.getBoundingClientRect();
        return rect.width < 44 || rect.height < 44 ? [{ text: element.textContent?.trim(), width: rect.width, height: rect.height }] : [];
      }));
      expect(undersized, `undersized targets at ${width}px on ${path}`).toEqual([]);
    }
  }
});

test("keeps product editing independent and confirms archival when the offer is active", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "offer lifecycle guard runs once in Chromium");
  const sellerId = "62000000-0000-4000-8000-000000000002";
  const productId = "63000000-0000-4000-8000-000000000002";
  const courseId = "64000000-0000-4000-8000-000000000002";
  const offerId = "65000000-0000-4000-8000-000000000002";
  const state = { offerStatus: "active", offerVersion: 3, transitions: [] as Array<Record<string, string>> };
  const detail = () => ({
    product: {
      id: productId, seller_organization_id: sellerId, slug: "curso-vivo", title: "Curso Vivo", description: "Publicado e à venda.", status: "published",
      version: 4, draft_version: 4,
      course: { id: courseId, title: "Fundamentos", description: "", status: "draft", draft_version: 2, position: 1 },
      courses: [{ id: courseId, title: "Fundamentos", description: "", status: "draft", draft_version: 2, position: 1 }],
      offer: { id: offerId, title: "Oferta Vivo", summary: "Termos.", status: state.offerStatus, version: state.offerVersion, draft_version: state.offerVersion, unit_amount_minor: 100, currency: "BRL", billing_period: "one_time" },
      publication: { id: "66000000-0000-4000-8000-000000000002", version: 1, published_at: "2026-07-11T15:00:00Z" },
      has_unpublished_changes: false,
      readiness: { can_publish: false, items: [{ key: "server", label: "Nenhuma alteração nova", ready: false }] }
    },
    csrf: { update: "csrf-update", publish: "csrf-publish", offer_status: "csrf-offer-status" }
  });

  await page.route(`**/api/sellers/${sellerId}/products/${productId}`, (route) => {
    if (route.request().method() === "GET") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(detail()) });
    return route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ error: "offer_not_editable" }) });
  });
  await page.route(`**/api/sellers/${sellerId}/products/${productId}/offers/${offerId}/status`, async (route) => {
    const posted = route.request().postDataBuffer();
    const form = await new Request(route.request().url(), { method: "PATCH", headers: route.request().headers(), body: posted ? new Uint8Array(posted) : undefined }).formData();
    state.transitions.push({ expected: String(form.get("expected_status")), next: String(form.get("status")), csrf: String(form.get("_csrf")) });
    state.offerStatus = String(form.get("status")); state.offerVersion += 1;
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ offer_id: offerId, status: state.offerStatus, offer_version: state.offerVersion, changed: true }) });
  });

  await page.setViewportSize({ width: 320, height: 568 });
  await enterAndWaitForSession(page);
  await page.goto(`/products/${sellerId}/${productId}`);
  await expect(page.getByRole("heading", { name: "Curso Vivo", exact: true })).toBeVisible();

  // Product editing remains independent from the already active offer.
  await expect(page.getByText("Edite o produto sem precisar configurar vendas.", { exact: false })).toBeVisible();
  await expect(page.getByLabel("Nome do produto")).toBeEnabled();
  await expect(page.getByRole("button", { name: "Salvar rascunho" })).toBeDisabled();

  // Archival requires explicit confirmation before the terminal transition.
  await page.getByRole("link", { name: /^Distribuição/ }).click();
  await page.getByRole("button", { name: "Encerrar vendas" }).click();
  const dialog = page.getByRole("dialog", { name: "Encerrar as vendas deste produto?" });
  await expect(dialog).toBeVisible();
  expect(state.transitions).toHaveLength(0);
  await page.getByRole("button", { name: "Cancelar" }).click();
  await expect(dialog).toBeHidden();
  expect(state.transitions).toHaveLength(0);

  await page.getByRole("button", { name: "Encerrar vendas" }).click();
  await page.getByRole("button", { name: "Arquivar definitivamente" }).click();
  await expect(page.getByText("Modo de acesso atualizado.")).toBeVisible();
  expect(state.transitions).toEqual([{ expected: "active", next: "archived", csrf: "csrf-offer-status" }]);
});

test("keeps product editing available while an offer transition is in flight", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "offer transition lock runs once in Chromium");
  const sellerId = "62000000-0000-4000-8000-000000000003";
  const productId = "63000000-0000-4000-8000-000000000003";
  const courseId = "64000000-0000-4000-8000-000000000003";
  const offerId = "65000000-0000-4000-8000-000000000003";
  const state = { offerStatus: "draft", offerVersion: 2 };
  let releaseStatus: () => void = () => {};
  let releaseReload: () => void = () => {};
  const detail = () => ({
    product: {
      id: productId, seller_organization_id: sellerId, slug: "curso-tr", title: "Curso TR", description: "Rascunho.", status: "draft",
      version: 3, draft_version: 3,
      course: { id: courseId, title: "Fundamentos", description: "", status: "draft", draft_version: 2, position: 1 },
      courses: [{ id: courseId, title: "Fundamentos", description: "", status: "draft", draft_version: 2, position: 1 }],
      offer: { id: offerId, title: "Oferta TR", summary: "Termos.", status: state.offerStatus, version: state.offerVersion, draft_version: state.offerVersion, unit_amount_minor: 100, currency: "BRL", billing_period: "one_time" },
      publication: { id: "66000000-0000-4000-8000-000000000003", version: 1, published_at: "2026-07-11T15:00:00Z" },
      has_unpublished_changes: false,
      readiness: { can_publish: true, items: [{ key: "server", label: "Pronto", ready: true }] }
    },
    csrf: { update: "csrf-update", publish: "csrf-publish", offer_status: "csrf-offer-status" }
  });

  let getCount = 0;
  await page.route(`**/api/sellers/${sellerId}/products/${productId}`, async (route) => {
    if (route.request().method() !== "GET") return route.fulfill({ status: 422, contentType: "application/json", body: "{}" });
    getCount += 1;
    // First GET (initial load) resolves immediately; the reconciliation GET after
    // the transition is held so the "transitioning" window stays observable.
    if (getCount >= 2 && state.offerStatus === "active") {
      await new Promise<void>((resolve) => { releaseReload = resolve; });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(detail()) });
  });
  await page.route(`**/api/sellers/${sellerId}/products/${productId}/offers/${offerId}/status`, async (route) => {
    await new Promise<void>((resolve) => { releaseStatus = resolve; });
    state.offerStatus = "active"; state.offerVersion += 1;
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ offer_id: offerId, status: "active", offer_version: state.offerVersion, changed: true }) });
  });

  await page.setViewportSize({ width: 320, height: 568 });
  await enterAndWaitForSession(page);
  await page.goto(`/products/${sellerId}/${productId}?section=publicar`);
  await expect(page.getByRole("heading", { name: "Acesso gratuito" })).toBeVisible();
  await page.getByRole("button", { name: "Ativar vendas e usar checkout" }).click();
  await expect(page.getByRole("button", { name: "Ativando vendas…" })).toBeVisible();

  // While the PATCH is in flight, product editing remains a separate concern.
  await page.getByRole("navigation", { name: "Áreas do produto" }).getByRole("link", { name: /^Produto/ }).click();
  await expect(page.getByLabel("Nome do produto")).toBeEnabled();
  await expect(page.getByRole("button", { name: "Salvar rascunho" })).toBeDisabled();

  // Release PATCH; the reconciliation GET is still held → editing stays available.
  releaseStatus();
  await expect.poll(() => getCount).toBeGreaterThanOrEqual(2);
  await expect(page.getByLabel("Nome do produto")).toBeEnabled();

  // Release the reconciliation GET; offer is now active → product stays editable.
  releaseReload();
  await expect(page.getByText("Edite o produto sem precisar configurar vendas.", { exact: false })).toBeVisible();
  await expect(page.getByLabel("Nome do produto")).toBeEnabled();
});

test("offers an in-flow refresh after an offer version conflict", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "offer conflict recovery runs once in Chromium");
  const sellerId = "62000000-0000-4000-8000-000000000005";
  const productId = "63000000-0000-4000-8000-000000000005";
  const courseId = "64000000-0000-4000-8000-000000000005";
  const offerId = "65000000-0000-4000-8000-000000000005";
  const state = { offerStatus: "draft", offerVersion: 2 };
  const detail = () => ({
    product: {
      id: productId, seller_organization_id: sellerId, slug: "curso-conflito", title: "Curso Conflito", description: "Rascunho.", status: "published",
      version: 3, draft_version: 3,
      course: { id: courseId, title: "Fundamentos", description: "", status: "draft", draft_version: 2, position: 1 },
      courses: [{ id: courseId, title: "Fundamentos", description: "", status: "draft", draft_version: 2, position: 1 }],
      offer: { id: offerId, title: "Oferta Conflito", summary: "Termos.", status: state.offerStatus, version: state.offerVersion, draft_version: state.offerVersion, unit_amount_minor: 100, currency: "BRL", billing_period: "one_time" },
      publication: { id: "66000000-0000-4000-8000-000000000005", version: 1, published_at: "2026-07-11T15:00:00Z" },
      has_unpublished_changes: false,
      readiness: { can_publish: false, items: [{ key: "server", label: "Nenhuma alteração nova", ready: false }] }
    },
    csrf: { update: "csrf-update", publish: "csrf-publish", offer_status: "csrf-offer-status" }
  });

  await page.route(`**/api/sellers/${sellerId}/products/${productId}`, (route) => route.request().method() === "GET"
    ? route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(detail()) })
    : route.fulfill({ status: 422, contentType: "application/json", body: "{}" }));
  await page.route(`**/api/sellers/${sellerId}/products/${productId}/offers/${offerId}/status`, (route) => {
    state.offerStatus = "active";
    state.offerVersion = 3;
    return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: "version_conflict" }) });
  });

  await page.setViewportSize({ width: 320, height: 568 });
  await enterAndWaitForSession(page);
  await page.goto(`/products/${sellerId}/${productId}?section=publicar`);
  await expect(page.getByRole("heading", { name: "Acesso gratuito" })).toBeVisible();
  await page.getByRole("button", { name: "Ativar vendas e usar checkout" }).click();
  await expect(page.getByText("O preço mudou em outra sessão.", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Atualizar dados do preço" })).toBeVisible();

  await page.getByRole("button", { name: "Atualizar dados do preço" }).click();
  await expect(page.getByRole("heading", { name: "Vendas ativas" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Desativar vendas e voltar ao gratuito" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Atualizar dados do preço" })).toHaveCount(0);
});

test("preserves a non-BRL offer currency on save and labels the price in that currency", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "currency preservation runs once in Chromium");
  const sellerId = "62000000-0000-4000-8000-000000000004";
  const productId = "63000000-0000-4000-8000-000000000004";
  const courseId = "64000000-0000-4000-8000-000000000004";
  const offerId = "65000000-0000-4000-8000-000000000004";
  let savedCurrency = "";
  const detail = () => ({
    product: {
      id: productId, seller_organization_id: sellerId, slug: "curso-usd", title: "Curso USD", description: "Rascunho.", status: "draft",
      version: 3, draft_version: 3,
      course: { id: courseId, title: "Fundamentos", description: "", status: "draft", draft_version: 2, position: 1 },
      courses: [{ id: courseId, title: "Fundamentos", description: "", status: "draft", draft_version: 2, position: 1 }],
      offer: { id: offerId, title: "Oferta USD", summary: "Termos.", status: "draft", version: 2, draft_version: 2, unit_amount_minor: 4990, currency: "USD", billing_period: "one_time" },
      publication: null, has_unpublished_changes: true,
      readiness: { can_publish: true, items: [{ key: "server", label: "Pronto", ready: true }] }
    },
    csrf: { update: "csrf-update", publish: "csrf-publish", offer_status: "csrf-offer-status" }
  });
  await page.route(`**/api/sellers/${sellerId}/products/${productId}`, async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(detail()) });
    const posted = route.request().postDataBuffer();
    const form = await new Request(route.request().url(), { method: "PATCH", headers: route.request().headers(), body: posted ? new Uint8Array(posted) : undefined }).formData();
    savedCurrency = String(form.get("offer_currency"));
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ product_id: productId, course_id: courseId, offer_id: offerId, product_version: 4, course_version: 3, offer_version: 3 }) });
  });

  await page.setViewportSize({ width: 320, height: 568 });
  await enterAndWaitForSession(page);
  await page.goto(`/products/${sellerId}/${productId}?section=oferta`);
  // Price label is in the stored currency, not R$.
  await expect(page.getByText("Preço (USD)")).toBeVisible();
  await expect(page.getByLabel("Preço (USD)")).toHaveValue("49,90");
  await page.getByLabel("Nome no checkout").fill("Oferta USD 2");
  await page.getByRole("button", { name: "Salvar rascunho" }).click();
  await expect(page.getByText("Tudo salvo com segurança.")).toBeVisible();
  expect(savedCurrency).toBe("USD");
});
