import { expect, test } from "@playwright/test";

async function login(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByRole("link", { name: "Entrar no ambiente local" }).click();
  await expect(page).toHaveURL("/dashboard");
  await expect(page.getByRole("heading", { name: "Bom dia, creator." })).toBeVisible();
}

test("keeps the core journey usable without horizontal overflow", async ({ page }) => {
  await login(page);
  for (const target of [
    { path: "/dashboard", heading: "Bom dia, creator." },
    { path: "/products", heading: "Produtos" },
    { path: "/library", heading: "Minha biblioteca" },
  ]) {
    if (page.url() !== `http://localhost:3000${target.path}`) {
      await page.locator(`a[href="${target.path}"]:visible`).first().click();
      await expect(page).toHaveURL(target.path);
      await expect(page.getByRole("heading", { name: target.heading, exact: true })).toBeVisible();
    }
    const dimensions = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
    expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client);
  }
  await expect(page.getByRole("heading", { name: "Minha biblioteca" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Outros produtos de Autonom.ia Lab" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Launch Lab" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Growth Systems" })).toHaveCount(1);
});

test("does not render protected preview content for an anonymous request", async ({ request }) => {
  const response = await request.get("/dashboard", { maxRedirects: 0 });
  expect(response.status()).toBe(307);
  expect(response.headers().location).toBe("/");
  const body = await response.text();
  expect(body).not.toContain("Bom dia, creator.");
  expect(body).not.toContain("Creator OS");
});

test("exposes touch-sized mobile navigation and preserves deep links", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await login(page);
  const libraryLink = page.getByRole("navigation", { name: "Navegação móvel" }).getByRole("link", { name: "Minha biblioteca" });
  const box = await libraryLink.boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  await libraryLink.click();
  await expect(page).toHaveURL("/library");
  expect((await page.request.get("/api/session")).status()).toBe(200);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Minha biblioteca" })).toBeVisible();
});

test("supports keyboard skip navigation and reduced motion", async ({ page }) => {
  await login(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.reload();
  await expect(page.getByRole("heading", { name: "Bom dia, creator." })).toBeVisible();
  const skipLink = page.getByRole("link", { name: "Pular para o conteúdo" });
  await skipLink.focus();
  await expect(skipLink).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#conteudo")).toBeFocused();
  const transitionDuration = await page.locator(".primary-button").first().evaluate((element) => getComputedStyle(element).transitionDuration);
  expect(Number.parseFloat(transitionDuration)).toBeLessThanOrEqual(0.001);
});

test("keeps the protected shell mounted without a client-side session waterfall", async ({ page }) => {
  await login(page);
  let sessionRequests = 0;
  page.on("response", (response) => {
    if (response.url().endsWith("/api/session")) sessionRequests += 1;
  });
  await page.locator('a[href="/products"]:visible').first().click();
  await expect(page.getByRole("heading", { name: "Produtos", exact: true })).toBeVisible();
  expect(sessionRequests).toBe(0);
  await expect(page.locator(".session-loading")).toHaveCount(0);
});

test("keeps seller onboarding recoverable when the domain API is unavailable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await page.goto("/products");
  const submit = page.getByRole("button", { name: "Ativar meu espaço" });
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
  const products: Array<Record<string, unknown>> = [];
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
  await login(page);
  await page.goto("/products");
  await page.getByLabel("Espaço de vendas").selectOption({ label: "Studio Mobile · @studio-mobile" });
  await expect(page.getByRole("heading", { name: "Estruture sua próxima oferta" })).toBeVisible();

  await page.getByLabel("Espaço de vendas").focus();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Nome do produto")).toBeFocused();
  await page.keyboard.type("Rascunho do tenant A");
  await page.getByLabel("Espaço de vendas").selectOption({ label: "Studio B · @studio-b" });
  await expect(page.getByText("Você tem alterações não salvas.", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Espaço de vendas")).toHaveValue(sellerId);
  await expect(page.getByLabel("Nome do produto")).toHaveValue("Rascunho do tenant A");
  await page.getByRole("button", { name: "Descartar e trocar" }).click();
  await expect(page.getByLabel("Espaço de vendas")).toHaveValue(otherSellerId);
  await expect(page.getByLabel("Nome do produto")).toHaveValue("");
  await page.getByLabel("Espaço de vendas").selectOption({ label: "Studio Mobile · @studio-mobile" });

  await page.getByLabel("Nome do produto").focus();
  await page.keyboard.type("Creator OS Mobile");
  await page.getByLabel("Endereço do produto", { exact: false }).fill("creator-os-mobile");
  await page.getByLabel("Descrição curta").fill("Fluxo mobile persistente e privado.");
  await page.getByLabel("Nome do curso inicial").fill("Fundamentos Mobile");
  await page.getByRole("button", { name: "Criar produto e curso" }).click();
  await createStarted;
  await expect(page.getByLabel("Espaço de vendas")).toBeDisabled();
  releaseCreate?.();
  await expect(page.getByRole("heading", { name: "Creator OS Mobile" })).toBeVisible();
  await expect(page.getByText("Fundamentos Mobile", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Espaço de vendas")).toBeEnabled();
  await expect(page.getByLabel("Nome do produto")).toHaveValue("");

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
    scroll: document.documentElement.scrollWidth
  }));
  expect(scaled.scroll).toBeLessThanOrEqual(scaled.client);
});

test("covers the approved responsive widths", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "viewport matrix runs once in Chromium");
  await login(page);
  for (const width of [360, 390, 768, 1024, 1440]) {
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
