import { mkdir } from "node:fs/promises";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

const password = process.env.E2E_REAL_IDENTITY_PASSWORD;
const controlToken = process.env.E2E_REAL_CONTROL_TOKEN;
const sellOrigin = process.env.E2E_REAL_SELL_ORIGIN ?? "http://localhost:3200";
const authOrigin = process.env.E2E_REAL_AUTH_ORIGIN ?? "http://127.0.0.1:3217";
const providerCredential = /demo-(?:access|id|refresh)-[A-Za-z0-9_-]{20,}/i;
const evidenceDirectory = path.resolve(
  process.env.E2E_REAL_EVIDENCE_DIR ?? "output/playwright/identity-real-evidence"
);
const expectedLessonTitles = [
  "O que é Codex e quando usar",
  "Como preparar contexto, repo e objetivo",
  "Como pedir implementação com segurança",
  "Como revisar testes, PR e evidências",
  "Como operar deploy e rollback"
] as const;

test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  if (!password) throw new Error("E2E_REAL_IDENTITY_PASSWORD was not initialized by the cross-repo runner.");
  if (!controlToken) throw new Error("E2E_REAL_CONTROL_TOKEN was not initialized by the cross-repo runner.");
  await mkdir(evidenceDirectory, { recursive: true });
});

test("admin demo crosses Auth and Foundation into the Autonom.ia Sell dashboard", async ({ context, page }) => {
  const evidence = observeReadableResponses(page);
  const callbackUrls: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.origin === sellOrigin && url.pathname === "/auth/callback") {
      callbackUrls.push(url.toString());
    }
  });

  await login(page, "admin.demo@autonomia.site", "/dashboard");
  await expect(page.getByRole("heading", { name: "Seu negócio em um só lugar." })).toBeVisible();
  await assertProtectedBrowserSurface(context, page, "admin");
  await page.screenshot({ path: path.join(evidenceDirectory, "admin-dashboard.png"), fullPage: true });
  expect(callbackUrls).toHaveLength(1);
  expect(page.url()).not.toMatch(/[?&](?:code|state)=/);

  // Replaying the callback URL after a successful login no longer answers with
  // raw JSON: the transaction cookie is already gone, which is the same shape a
  // sign-up hits when it outlives the transaction, so the app restarts the
  // login. What matters for security is unchanged and asserted here — the
  // replay must not mint a session, and the spent code is never exchanged again.
  const replay = await page.request.get(callbackUrls[0]!, { maxRedirects: 0 });
  expect(replay.status()).toBe(307);
  expect(replay.headers()["location"]).toContain("/auth/login");
  expect(replay.headers()["set-cookie"] ?? "").not.toContain("appsell_session=");
  expect(replay.headers()["set-cookie"] ?? "").toContain("appsell_oauth_recovery=");

  // Second replay, now with the guard armed: falls through to the error body
  // instead of bouncing the browser.
  const guardedReplay = await page.request.get(callbackUrls[0]!, { maxRedirects: 0 });
  expect(guardedReplay.status()).toBe(400);
  expect(guardedReplay.headers()["set-cookie"] ?? "").not.toContain("appsell_session=");

  const concurrentRefresh = await Promise.all([
    refreshIdentitySession(page, 1),
    refreshIdentitySession(page, 1)
  ]);
  expect(concurrentRefresh).toEqual([2, 2]);
  expect(await refreshIdentitySession(page, 2)).toBe(3);

  const sellersResponse = await page.request.get("/api/sellers");
  expect(sellersResponse.status()).toBe(200);
  const sellers = await sellersResponse.json() as { sellers: Array<{ id: string; role: string }> };
  expect(sellers.sellers).toHaveLength(1);
  expect(sellers.sellers[0]).toMatchObject({ role: "seller_owner" });
  const foreignTenant = await page.request.get(
    "/api/sellers/70000000-0000-4000-8000-000000000099/products"
  );
  expect(foreignTenant.status()).toBe(403);
  expect(await foreignTenant.json()).toEqual({ error: "demo_admin_capability_forbidden" });
  await evidence.assertClean();
});

test("user demo crosses Auth and Foundation into the Autonom.ia Sell library", async ({ context, page }) => {
  const evidence = observeReadableResponses(page);
  await login(page, "user.demo@autonomia.site", "/library");
  await expect(page.getByRole("heading", { name: "Minha biblioteca" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "Codex na prática", exact: true })).toBeVisible();
  await assertProtectedBrowserSurface(context, page, "buyer");
  await page.screenshot({ path: path.join(evidenceDirectory, "buyer-library.png"), fullPage: true });
  await page.getByRole("link", { name: "Começar curso" }).click();
  await expect(page.getByRole("heading", { level: 1, name: expectedLessonTitles[0] })).toBeVisible();
  await expect(page.getByText("Aula 1 de 5", { exact: true })).toBeVisible();
  await page.screenshot({ path: path.join(evidenceDirectory, "buyer-first-lesson.png"), fullPage: true });

  const lessonLinks = page.locator(".player-outline-mobile nav[aria-label='Aulas do curso'] a");
  await expect(lessonLinks).toHaveCount(expectedLessonTitles.length);
  const lessonHrefs = await lessonLinks.evaluateAll((links) => links.map((link) => link.getAttribute("href")));
  expect(lessonHrefs.every((href) => /^\/library\/items\/[0-9a-f-]+\/lessons\/[0-9a-f-]+$/.test(href ?? ""))).toBe(true);

  for (const [index, lessonHref] of lessonHrefs.entries()) {
    await page.goto(lessonHref!);
    await expect(page.getByRole("heading", { level: 1, name: expectedLessonTitles[index] })).toBeVisible();
    await expect(page.getByText(`Aula ${index + 1} de ${expectedLessonTitles.length}`, { exact: true })).toBeVisible();
    const pdf = page.locator(".protected-pdf-page img");
    await expect(pdf).toHaveCount(1);
    const pdfHref = await pdf.getAttribute("src");
    expect(pdfHref).toMatch(/^\/api\/library\/items\/[0-9a-f-]+\/assets\/[0-9a-f-]+\/pages\/1$/);
    await expect(page.getByRole("link", { name: /Baixar PDF/ }))
      .toHaveCount(index === expectedLessonTitles.length - 1 ? 1 : 0);
    const pdfResponse = await page.request.get(pdfHref!);
    expect(pdfResponse.status()).toBe(200);
    expect(pdfResponse.headers()["content-type"]).toBe("image/png");
    const originalResponse = await page.request.get(pdfHref!.replace(/\/pages\/1$/, ""));
    expect(originalResponse.status()).toBe(404);
  }
  await evidence.assertClean();
});

test("superadmin demo crosses Auth and Foundation into a temporary read-only inspection", async ({ context, page }) => {
  const evidence = observeReadableResponses(page);
  await login(page, "comercial@autonomia.site", "/platform/demo-inspection");
  await expect(page.getByRole("heading", { level: 1, name: "Inspeção da tenant demo" })).toBeVisible();
  await assertProtectedBrowserSurface(context, page, "superadmin");

  const sellersResponse = await page.request.get("/api/sellers");
  expect(sellersResponse.status()).toBe(403);
  expect(await sellersResponse.json()).toEqual({ error: "demo_superadmin_capability_forbidden" });

  await page.getByRole("button", { name: "Iniciar inspeção read-only" }).click();
  await expect(page).toHaveURL(/\/platform\/demo-inspection\?status=started$/);
  await expect(page.getByRole("heading", { level: 2, name: "Autonom.ia Demo" })).toBeVisible();
  await expect(page.getByText("Codex na prática", { exact: true })).toBeVisible();
  await page.screenshot({ path: path.join(evidenceDirectory, "superadmin-demo-inspection.png"), fullPage: true });

  await page.getByRole("button", { name: "Encerrar e revogar acesso" }).click();
  await expect(page).toHaveURL(/\/platform\/demo-inspection\?status=revoked$/);
  await expect(page.getByText("Inspeção encerrada e token revogado.")).toBeVisible();
  await evidence.assertClean();
});

async function login(page: Page, email: string, expectedPath: string) {
  await page.goto("/");
  await page.getByRole("link", { name: "Entrar na sua conta" }).click();
  await expect(page).toHaveURL((url) => url.origin === authOrigin && url.pathname === "/login/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Senha").fill(password!);
  await page.getByRole("button", { name: "Entrar" }).click();
  await expect(page).toHaveURL((url) => url.origin === sellOrigin && url.pathname === expectedPath, {
    timeout: 60_000
  });
}

async function assertProtectedBrowserSurface(
  context: import("@playwright/test").BrowserContext,
  page: Page,
  persona: "admin" | "buyer" | "superadmin"
) {
  const cookies = await context.cookies();
  const session = cookies.find((cookie) => cookie.name === "appsell_session");
  expect(session).toMatchObject({ httpOnly: true, sameSite: "Lax" });
  expect(session?.value).toMatch(/^[A-Za-z0-9_-]{43}$/);
  expect(cookies.some((cookie) => providerCredential.test(cookie.value))).toBe(false);
  expect(JSON.stringify(await context.storageState())).not.toMatch(providerCredential);

  const readable = await page.evaluate(() => ({
    cookie: document.cookie,
    dom: document.documentElement.innerHTML,
    local: Object.fromEntries(Object.entries(localStorage)),
    session: Object.fromEntries(Object.entries(sessionStorage)),
    url: location.href
  }));
  expect(readable.cookie).not.toContain("appsell_session");
  expect(JSON.stringify(readable)).not.toMatch(providerCredential);
  expect(readable.url).not.toMatch(/[?&](?:code|state)=/);

  const publicSession = await page.request.get("/api/session");
  expect(publicSession.status()).toBe(200);
  expect(await publicSession.json()).toMatchObject({
    authenticated: true,
    demoProfile: { persona }
  });
}

async function refreshIdentitySession(page: Page, expectedRevision: number) {
  const response = await page.request.post("/dev/identity/refresh", {
    headers: {
      "x-autonomia-e2e-control": controlToken!,
      "x-autonomia-e2e-expected-revision": String(expectedRevision)
    }
  });
  const responseText = await response.text();
  expect(response.status(), responseText).toBe(200);
  const body = JSON.parse(responseText) as { refreshed: boolean; revision: number };
  expect(body.refreshed).toBe(true);
  return body.revision;
}

function observeReadableResponses(page: Page) {
  const reads: Promise<string | null>[] = [];
  page.on("response", (response) => {
    const contentType = response.headers()["content-type"] ?? "";
    reads.push(Promise.resolve(JSON.stringify({ url: response.url(), headers: response.headers() })));
    if (!/(?:text|json|javascript)/i.test(contentType) || /text\/event-stream/i.test(contentType)) return;
    reads.push(Promise.race([
      response.text().catch(() => null),
      new Promise<null>((resolve) => setTimeout(() => resolve(null), 5_000))
    ]));
  });
  return {
    async assertClean() {
      const bodies = (await Promise.all(reads)).filter((body): body is string => body !== null);
      expect(bodies.join("\n")).not.toMatch(providerCredential);
    }
  };
}
