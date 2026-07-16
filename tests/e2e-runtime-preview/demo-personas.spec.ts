import { createHash } from "node:crypto";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

const evidenceDirectory = path.resolve(
  process.env.E2E_RUNTIME_EVIDENCE_DIR ?? "output/playwright/runtime-preview-evidence"
);
const runtimeOrigin = new URL(process.env.E2E_RUNTIME_ORIGIN ?? "http://localhost:13200");
const loopbackHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);
const secureRuntime = runtimeOrigin.protocol === "https:";

if (!secureRuntime && !(runtimeOrigin.protocol === "http:" && loopbackHosts.has(runtimeOrigin.hostname))) {
  throw new Error(
    `E2E_RUNTIME_ORIGIN must use HTTPS unless it is an HTTP loopback URL; received ${runtimeOrigin.origin}`
  );
}
const sessionCookieName = secureRuntime ? "__Host-appsell_session" : "appsell_session";
const inspectionCookieName = secureRuntime
  ? "__Host-appsell_demo_inspection"
  : "appsell_demo_inspection";
const expectedPdf = {
  bytes: 3067,
  sha256: "59cabda6e11e2b1a1ee2af480e4f242dd1ac941ae62ab20e06f12104f691b270"
};
const expectedVideo = {
  bytes: 505682,
  sha256: "86b6f3efde35bafc18cec019a3f95ea50c5e89721f28cffb04f2b5f611ba97bd"
};

test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  await mkdir(evidenceDirectory, { recursive: true });
});

test("admin demo entra no tenant correto", async ({ context, page }) => {
  await login(page, "admin", "/dashboard");
  await expect(page.getByRole("heading", { name: "Seu negócio em um só lugar." })).toBeVisible();
  await expect(page.getByRole("note")).toContainText("admin.demo@autonomia.site");
  const sellers = await page.request.get("/api/sellers");
  expect(sellers.status()).toBe(200);
  expect((await sellers.json()) as unknown).toMatchObject({
    sellers: [{
      slug: "autonomia-demo",
      display_name: "Autonom.ia Demo",
      role: "seller_owner"
    }]
  });
  await assertOpaqueSession(context, page, "admin");
  await assertMobileWidth(page);
  await page.screenshot({ path: path.join(evidenceDirectory, "admin-dashboard.png"), fullPage: true });
});

test("buyer demo abre PDF e vídeo protegidos", async ({ context, page }) => {
  await login(page, "buyer", "/library");
  await expect(page.getByRole("note")).toContainText(
    "DEMO · Aluno · user.demo@autonomia.site"
  );
  await expect(page.getByText("Não foi possível validar seu acesso.")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Sua biblioteca está vazia" })).toBeVisible();
  await page.getByRole("link", { name: "Comprar com cupom demo" }).click();
  await expect(page).toHaveURL((url) => url.pathname === "/checkout");
  await expect(page.getByRole("heading", { name: "Codex na prática" })).toBeVisible();
  await page.getByLabel("Cupom de demonstração").fill("AUTONOMIA100");
  await page.getByRole("button", { name: "Concluir compra por R$ 0,00" }).click();
  await expect(page.getByRole("heading", { name: "Compra demo aprovada" })).toBeVisible({
    timeout: 15_000
  });
  await page.getByRole("link", { name: "Abrir minha biblioteca" }).click();
  await expect(page).toHaveURL((url) => url.pathname === "/library");
  await expect(page.getByRole("heading", { name: "Minha biblioteca" })).toBeVisible();
  await page.getByRole("link", { name: "Começar curso" }).click();
  await expect(page.getByText("Aula 1 de 5", { exact: true })).toBeVisible();
  const mobileOutline = page.locator(".player-outline-mobile");
  await mobileOutline.locator("summary").click();
  await mobileOutline.getByRole("link", {
    name: "Como operar deploy e rollback"
  }).click();
  await expect(page.getByText("Aula 5 de 5", { exact: true })).toBeVisible();

  const pdf = page.getByRole("link", { name: /Abrir PDF/ });
  await expect(pdf).toHaveCount(1);
  const pdfHref = await pdf.getAttribute("href");
  expect(pdfHref).toMatch(/^\/api\/library\/items\/[0-9a-f-]+\/assets\/[0-9a-f-]+$/);
  const pdfResponse = await page.request.get(pdfHref!);
  expect(pdfResponse.status()).toBe(200);
  expect(pdfResponse.headers()["content-type"]).toBe("application/pdf");
  const pdfBody = await pdfResponse.body();
  expect(pdfBody.byteLength).toBe(expectedPdf.bytes);
  expect(pdfBody.subarray(0, 5).toString("ascii")).toBe("%PDF-");
  expect(createHash("sha256").update(pdfBody).digest("hex")).toBe(expectedPdf.sha256);

  const video = page.locator('video[aria-label="Codex em 3 passos"]');
  await expect(video).toHaveCount(1);
  const videoHref = await video.getAttribute("src");
  expect(videoHref).toMatch(/^\/api\/library\/items\/[0-9a-f-]+\/assets\/[0-9a-f-]+$/);
  const videoResponse = await page.request.get(videoHref!, {
    headers: { range: "bytes=0-1023" }
  });
  expect(videoResponse.status()).toBe(206);
  expect(videoResponse.headers()["content-type"]).toBe("video/mp4");
  expect(videoResponse.headers()["content-range"]).toBe(`bytes 0-1023/${expectedVideo.bytes}`);
  const videoPrefix = await videoResponse.body();
  expect(videoPrefix.byteLength).toBe(1024);
  expect(videoPrefix.subarray(4, 8).toString("ascii")).toBe("ftyp");
  const completeVideo = await page.request.get(videoHref!);
  expect(completeVideo.status()).toBe(200);
  const videoBody = await completeVideo.body();
  expect(videoBody.byteLength).toBe(expectedVideo.bytes);
  expect(createHash("sha256").update(videoBody).digest("hex")).toBe(expectedVideo.sha256);

  await assertOpaqueSession(context, page, "buyer");
  await assertMobileWidth(page);
  await page.screenshot({ path: path.join(evidenceDirectory, "buyer-player.png"), fullPage: true });
});

test("superadmin demo inspeciona e revoga sem impersonar", async ({ context, page }) => {
  await login(page, "superadmin", "/platform/demo-inspection");
  await expect(page.getByRole("heading", { name: "Inspeção da tenant demo" })).toBeVisible();
  const profile = await page.request.get("/api/session");
  expect(profile.status()).toBe(200);
  expect(await profile.json()).toMatchObject({
    authenticated: true,
    demoProfile: { persona: "superadmin", email: "comercial@autonomia.site" }
  });
  await page.getByRole("button", { name: "Iniciar inspeção read-only" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Autonom.ia Demo" })).toBeVisible();
  await expect(page.getByText("Codex na prática", { exact: true })).toBeVisible();
  const activeCookies = await context.cookies();
  const sessionCookie = activeCookies.find((cookie) => cookie.name === sessionCookieName);
  const inspectionCookie = activeCookies.find(
    (cookie) => cookie.name === inspectionCookieName
  );
  expect(sessionCookie?.value).toMatch(/^[A-Za-z0-9_-]{43}$/);
  expect(inspectionCookie?.value).toMatch(/^[0-9a-f-]{36}\.[A-Za-z0-9_-]{43}$/);
  expect(sessionCookie).toMatchObject({ secure: secureRuntime, httpOnly: true, path: "/" });
  expect(inspectionCookie).toMatchObject({
    secure: secureRuntime,
    httpOnly: true,
    sameSite: "Strict",
    path: "/"
  });
  await page.getByRole("button", { name: "Encerrar e revogar acesso" }).click();
  await expect(page.getByText("Inspeção encerrada e token revogado.")).toBeVisible();
  const replay = await page.request.get("/api/platform/demo-inspections", {
    headers: {
      cookie: `${sessionCookieName}=${sessionCookie!.value}; ${inspectionCookieName}=${inspectionCookie!.value}`
    }
  });
  expect(replay.status()).toBe(200);
  expect(await replay.json()).toMatchObject({
    authorized: true,
    ticket_status: "expired",
    inspection: null
  });
  await assertOpaqueSession(context, page, "superadmin");
  await assertMobileWidth(page);
  await page.screenshot({
    path: path.join(evidenceDirectory, "superadmin-inspection.png"),
    fullPage: true
  });
});

async function login(page: Page, persona: "admin" | "buyer" | "superadmin", pathName: string) {
  await page.goto(`/auth/login?persona=${persona}`);
  await expect(page).toHaveURL((url) => url.pathname === pathName);
}

async function assertOpaqueSession(
  context: import("@playwright/test").BrowserContext,
  page: Page,
  persona: "admin" | "buyer" | "superadmin"
) {
  const session = (await context.cookies()).find(
    (cookie) => cookie.name === sessionCookieName
  );
  expect(session).toMatchObject({
    httpOnly: true,
    secure: secureRuntime,
    sameSite: "Lax",
    path: "/"
  });
  expect(session?.value).toMatch(/^[A-Za-z0-9_-]{43}$/);
  expect(await page.evaluate(() => document.cookie)).not.toContain(sessionCookieName);
  const response = await page.request.get("/api/session");
  expect(response.status()).toBe(200);
  expect(await response.json()).toMatchObject({
    authenticated: true,
    demoProfile: { persona }
  });
}

async function assertMobileWidth(page: Page) {
  await page.setViewportSize({ width: 320, height: 800 });
  const width = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth
  }));
  expect(width.scroll).toBeLessThanOrEqual(width.client);
}
