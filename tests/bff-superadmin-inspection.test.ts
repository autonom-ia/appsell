import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes, randomUUID } from "node:crypto";
import { readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { chromium, expect as pwExpect, type Browser, type Page } from "@playwright/test";
import { expect, test } from "vitest";

import manifest from "../lib/demo-codex-assets.json";
import demoManifest from "../demo/content/codex-na-pratica/manifest.json";
import { deriveCsrfToken } from "../lib/csrf";
import { startIsolatedRedis, unusedNonDefaultPort } from "./helpers/isolated-redis";

const domainApiUrl = process.env.APPSELL_TEST_DOMAIN_API_URL;
const CREATE_PRODUCT_DRAFT_CSRF_ACTION = "POST:/api/products";
const UPDATE_CURRICULUM_CSRF_ACTION = "PATCH:/api/curriculum";

test("fails a demo superadmin callback closed without the Domain API", async () => {
  const generatedFiles = ["next-env.d.ts", "tsconfig.json"];
  const originals = await Promise.all(generatedFiles.map((file) => readFile(join(process.cwd(), file))));
  const redis = await startIsolatedRedis();
  const port = await unusedNonDefaultPort();
  const suffix = `${process.pid}-${Date.now()}`;
  const distDir = `.next-inspection-no-domain-${suffix}`;
  const origin = `http://localhost:${port}`;
  const server = startBff(port, distDir, {
    APPSELL_REDIS_URL: redis.url,
    APPSELL_REDIS_PREFIX: `appsell:test:inspection-no-domain-${suffix}`,
    APPSELL_SESSION_HMAC_KEY_B64: randomBytes(32).toString("base64"),
    APPSELL_CSRF_HMAC_KEY_B64: randomBytes(32).toString("base64"),
    APPSELL_PUBLIC_ORIGIN: origin,
    APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: JSON.stringify([
      { id: "test-v1", key: randomBytes(32).toString("base64"), active: true }
    ]),
    APPSELL_DOMAIN_API_URL: "",
    APP_ENV: "ci",
    APPSELL_DEMO_ENABLED: "true",
    APPSELL_DEMO_SUPERADMIN_ENABLED: "true",
    NEXT_TELEMETRY_DISABLED: "1"
  });

  try {
    await waitForBff(server, port);
    const missingPersona = await fetch(`${origin}/auth/login`, { redirect: "manual" });
    expect(missingPersona.status).toBe(400);
    expect(await missingPersona.json()).toEqual({ error: "demo_persona_required" });
    const start = await fetch(`${origin}/auth/login?persona=superadmin`, { redirect: "manual" });
    const transaction = cookiePair(start.headers.get("set-cookie"), "appsell_oauth_tx");
    const authorize = await fetch(requiredHeader(start, "location"), { redirect: "manual" });
    const callback = await fetch(requiredHeader(authorize, "location"), {
      redirect: "manual",
      headers: { cookie: transaction }
    });
    expect(callback.status).toBe(503);
    expect(await callback.json()).toEqual({ error: "domain_api_not_configured" });
    const setCookies = callback.headers.getSetCookie();
    expect(setCookies.find((cookie) => cookie.startsWith("appsell_session="))).toContain("Max-Age=0");
    for (const cookieName of [
      "appsell_seller_csrf",
      "appsell_product_csrf",
      "appsell_product_update_csrf",
      "appsell_product_publish_csrf"
    ]) {
      expect(setCookies.find((cookie) => cookie.startsWith(`${cookieName}=`))).toContain("Max-Age=0");
    }
  } finally {
    await stopBff(server);
    await redis.stop();
    await rm(join(process.cwd(), distDir), { recursive: true, force: true });
    await Promise.all(generatedFiles.map((file, index) => writeFile(join(process.cwd(), file), originals[index] ?? "")));
  }
}, 60_000);

test.skipIf(!domainApiUrl)("keeps superadmin demo inspection temporary, read-only and mobile-safe", async () => {
  const generatedFiles = ["next-env.d.ts", "tsconfig.json"];
  const originals = await Promise.all(generatedFiles.map((file) => readFile(join(process.cwd(), file))));
  const redis = await startIsolatedRedis();
  const port = await unusedNonDefaultPort();
  const suffix = `${process.pid}-${Date.now()}`;
  const distDir = `.next-inspection-${suffix}`;
  const origin = `http://localhost:${port}`;
  const csrfKey = randomBytes(32);
  const server = startBff(port, distDir, {
    APPSELL_REDIS_URL: redis.url,
    APPSELL_REDIS_PREFIX: `appsell:test:inspection-${suffix}`,
    APPSELL_SESSION_HMAC_KEY_B64: randomBytes(32).toString("base64"),
    APPSELL_CSRF_HMAC_KEY_B64: csrfKey.toString("base64"),
    APPSELL_PUBLIC_ORIGIN: origin,
    APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: JSON.stringify([
      { id: "test-v1", key: randomBytes(32).toString("base64"), active: true }
    ]),
    APPSELL_DOMAIN_API_URL: requiredEnvironment("APPSELL_TEST_DOMAIN_API_URL"),
    APPSELL_INTERNAL_ISSUER: requiredEnvironment("APPSELL_INTERNAL_ISSUER"),
    APPSELL_INTERNAL_AUDIENCE: requiredEnvironment("APPSELL_INTERNAL_AUDIENCE"),
    APPSELL_IDENTITY_ISSUER: requiredEnvironment("APPSELL_IDENTITY_ISSUER"),
    APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON: requiredEnvironment("APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON"),
    APPSELL_SERVICE_CREDENTIAL_B64: requiredEnvironment("APPSELL_SERVICE_CREDENTIAL_B64"),
    APP_ENV: "ci",
    APPSELL_DEMO_ENABLED: "true",
    APPSELL_DEMO_SUPERADMIN_ENABLED: "true",
    NEXT_TELEMETRY_DISABLED: "1"
  });
  let browser: Browser | undefined;

  try {
    await waitForBff(server, port);
    const login = await loginSuperadmin(origin);
    expect(login.redirect).toBe(`${origin}/platform/demo-inspection`);
    for (const cookieName of [
      "appsell_seller_csrf",
      "appsell_product_csrf",
      "appsell_product_update_csrf",
      "appsell_product_publish_csrf"
    ]) {
      expect(login.setCookies.find((cookie) => cookie.startsWith(`${cookieName}=`))).toContain("Max-Age=0");
    }
    const sessionId = cookieValue(login.session);
    const startCsrf = deriveCsrfToken(
      sessionId,
      "POST:/api/platform/demo-inspections",
      csrfKey
    );
    const forbiddenStart = await fetch(`${origin}/api/platform/demo-inspections`, {
      method: "POST",
      headers: { cookie: login.session, origin },
      body: new URLSearchParams({
        _csrf: "forged",
        reason: "Tentativa sem CSRF valido",
        ttl_seconds: "300"
      })
    });
    expect(forbiddenStart.status).toBe(403);

    const started = await fetch(`${origin}/api/platform/demo-inspections`, {
      method: "POST",
      redirect: "manual",
      headers: { cookie: login.session, origin },
      body: new URLSearchParams({
        _csrf: startCsrf,
        reason: "Validacao BFF da tenant demo em leitura controlada.",
        ttl_seconds: "300"
      })
    });
    expect(started.status, await started.clone().text()).toBe(303);
    const inspectionCookie = cookiePair(started.headers.get("set-cookie"), "appsell_demo_inspection");
    expect(started.headers.get("set-cookie")).toContain("HttpOnly");
    expect(started.headers.get("set-cookie")).toContain("SameSite=strict");
    const activeCookies = `${login.session}; ${inspectionCookie}`;
    const activePage = await fetch(`${origin}/platform/demo-inspection`, {
      headers: { cookie: activeCookies }
    });
    expect(activePage.status).toBe(200);
    const activeHtml = await activePage.text();
    const inspectionToken = inspectionCookie.slice(inspectionCookie.lastIndexOf(".") + 1);
    expect(inspectionToken).toHaveLength(43);
    expect(activeHtml).toContain("LOCAL/CI · READ-ONLY");
    expect(activeHtml).not.toContain("user.demo@autonomia.site");
    expect(activeHtml).not.toContain(inspectionToken);
    expect(activeHtml).not.toContain(sessionId);

    const safeAccess = await fetch(`${origin}/api/platform/demo-inspections`, {
      headers: { cookie: activeCookies }
    });
    expect(safeAccess.status).toBe(200);
    const safeAccessText = await safeAccess.text();
    expect(safeAccessText).toContain("Codex na prática");
    expect(safeAccessText).not.toContain(inspectionToken);
    expect(safeAccessText).not.toContain(sessionId);
    expect(safeAccessText).not.toContain("user.demo@autonomia.site");

    const sellerProducts = await fetch(
      `${origin}/api/sellers/${manifest[0]!.seller_id}/products`,
      { headers: { cookie: activeCookies } }
    );
    expect(sellerProducts.status).toBe(403);
    expect(await sellerProducts.json()).toEqual({ error: "demo_superadmin_capability_forbidden" });
    const forgedSellerCsrf = deriveCsrfToken(sessionId, "POST:/api/sellers", csrfKey);
    const forbiddenOnboarding = await fetch(`${origin}/api/sellers`, {
      method: "POST",
      headers: { cookie: activeCookies, origin },
      body: new URLSearchParams({
        _csrf: forgedSellerCsrf,
        idempotency_key: randomUUID(),
        slug: `superadmin-forbidden-${suffix}`,
        display_name: "Superadmin Must Not Own"
      })
    });
    expect(forbiddenOnboarding.status).toBe(403);
    expect(await forbiddenOnboarding.json()).toEqual({ error: "demo_superadmin_capability_forbidden" });

    const forbiddenProductCreate = await fetch(
      `${origin}/api/sellers/${demoManifest.seller.id}/products`,
      {
        method: "POST",
        headers: { cookie: activeCookies, origin },
        body: new URLSearchParams({
          _csrf: deriveCsrfToken(sessionId, CREATE_PRODUCT_DRAFT_CSRF_ACTION, csrfKey),
          idempotency_key: randomUUID(),
          slug: `superadmin-forbidden-product-${Date.now()}`,
          product_title: "Produto proibido",
          product_description: "Não pode ser criado pelo superadmin.",
          course_title: "Curso proibido"
        })
      }
    );
    expect(forbiddenProductCreate.status).toBe(403);
    expect(await forbiddenProductCreate.json()).toEqual({ error: "demo_superadmin_capability_forbidden" });

    const forbiddenCurriculumPatch = await fetch(
      `${origin}/api/sellers/${demoManifest.seller.id}/products/${demoManifest.product.id}` +
      `/courses/${demoManifest.course.id}/modules/${demoManifest.module.id}`,
      {
        method: "PATCH",
        headers: { cookie: activeCookies, origin },
        body: new URLSearchParams({
          _csrf: deriveCsrfToken(sessionId, UPDATE_CURRICULUM_CSRF_ACTION, csrfKey),
          idempotency_key: randomUUID(),
          expected_course_version: "1",
          title: "Módulo proibido"
        })
      }
    );
    expect(forbiddenCurriculumPatch.status).toBe(403);
    expect(await forbiddenCurriculumPatch.json()).toEqual({ error: "demo_superadmin_capability_forbidden" });

    const revokeCsrf = deriveCsrfToken(
      sessionId,
      "POST:/api/platform/demo-inspections/revoke",
      csrfKey
    );
    const revoked = await fetch(`${origin}/api/platform/demo-inspections/revoke`, {
      method: "POST",
      redirect: "manual",
      headers: { cookie: activeCookies, origin },
      body: new URLSearchParams({
        _csrf: revokeCsrf,
        reason: "Validacao BFF concluida e acesso encerrado."
      })
    });
    expect(revoked.status, await revoked.clone().text()).toBe(303);
    expect(revoked.headers.get("set-cookie")).toContain("Max-Age=0");

    browser = await chromium.launch();
    const context = await browser.newContext({ viewport: { width: 320, height: 720 } });
    const page = await context.newPage();
    await page.goto(origin);
    await pwExpect(page.getByRole("link", { name: /superadmin/i })).toHaveCount(0);
    await page.goto(`${origin}/auth/login?persona=superadmin`);
    await page.waitForURL(`${origin}/platform/demo-inspection`);
    await pwExpect(page.getByRole("heading", { name: "Inspeção da tenant demo" })).toBeVisible();
    await pwExpect(page.getByText("LOCAL/CI · READ-ONLY", { exact: true })).toBeVisible();
    await page.getByLabel("Motivo operacional").fill("Demonstração supervisionada no navegador mobile.");
    await page.getByRole("button", { name: "Iniciar inspeção read-only" }).click();
    await page.waitForURL(/status=started/);
    await pwExpect(page.getByRole("heading", { name: "Autonom.ia Demo" })).toBeVisible();
    await pwExpect(page.getByText("Codex na prática")).toBeVisible();
    await pwExpect(page.getByRole("link", { name: "Produtos" })).toHaveCount(0);
    await expectNoHorizontalOverflow(page);
    await page.goto(`${origin}/products`);
    await page.waitForURL(`${origin}/platform/demo-inspection`);
    await page.getByRole("button", { name: "Encerrar e revogar acesso" }).click();
    await page.waitForURL(/status=revoked/);
    await pwExpect(page.getByText("Inspeção encerrada e token revogado.")).toBeVisible();
    await context.close();
  } finally {
    await browser?.close();
    await stopBff(server);
    await redis.stop();
    await rm(join(process.cwd(), distDir), { recursive: true, force: true });
    await Promise.all(generatedFiles.map((file, index) => writeFile(join(process.cwd(), file), originals[index] ?? "")));
  }
}, 120_000);

async function loginSuperadmin(origin: string) {
  const start = await fetch(`${origin}/auth/login?persona=superadmin`, { redirect: "manual" });
  const transaction = cookiePair(start.headers.get("set-cookie"), "appsell_oauth_tx");
  const authorize = await fetch(requiredHeader(start, "location"), { redirect: "manual" });
  const callback = await fetch(requiredHeader(authorize, "location"), {
    redirect: "manual",
    headers: { cookie: transaction }
  });
  expect(callback.status, await callback.clone().text()).toBe(307);
  return {
    redirect: requiredHeader(callback, "location"),
    session: cookiePair(callback.headers.get("set-cookie"), "appsell_session"),
    setCookies: callback.headers.getSetCookie()
  };
}

function startBff(port: number, distDir: string, shared: Record<string, string>) {
  return spawn(process.execPath, [join(process.cwd(), "node_modules/next/dist/bin/next"), "dev", "-H", "127.0.0.1", "-p", String(port)], {
    cwd: process.cwd(),
    env: { ...process.env, ...shared, APPSELL_NEXT_DIST_DIR: distDir },
    stdio: ["ignore", "pipe", "pipe"]
  });
}

async function waitForBff(process: ChildProcess, port: number) {
  const deadline = Date.now() + 20_000;
  let diagnostics = "";
  process.stderr?.on("data", (chunk) => { diagnostics = `${diagnostics}${String(chunk)}`.slice(-3_000); });
  while (Date.now() < deadline) {
    if (process.exitCode !== null) throw new Error(`BFF ${port} exited: ${diagnostics}`);
    try {
      if ((await fetch(`http://127.0.0.1:${port}/`)).ok) return;
    } catch {
      // The dev server is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`BFF ${port} did not become ready: ${diagnostics}`);
}

async function stopBff(process: ChildProcess) {
  if (process.exitCode !== null) return;
  process.kill("SIGTERM");
  await Promise.race([
    new Promise<void>((resolve) => process.once("exit", () => resolve())),
    new Promise<void>((resolve) => setTimeout(resolve, 5_000))
  ]);
  if (process.exitCode === null) {
    process.kill("SIGKILL");
    await new Promise<void>((resolve) => process.once("exit", () => resolve()));
  }
}

async function expectNoHorizontalOverflow(page: Page) {
  expect(await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth
  }))).toEqual({ scrollWidth: 320, clientWidth: 320 });
}

function cookiePair(serialized: string | null, name: string) {
  const match = serialized?.match(new RegExp(`(?:^|,\\s*)(${name}=[^;]*)`));
  if (!match?.[1]) throw new Error(`missing ${name} cookie`);
  return match[1];
}

function cookieValue(pair: string) {
  return pair.slice(pair.indexOf("=") + 1);
}

function requiredHeader(response: Response, name: string) {
  const value = response.headers.get(name);
  if (!value) throw new Error(`missing ${name} header`);
  return value;
}

function requiredEnvironment(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`missing ${name}`);
  return value;
}
