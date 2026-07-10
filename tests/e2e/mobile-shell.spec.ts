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
