import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import { readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { expect, test } from "vitest";
import { chromium, expect as pwExpect, type Browser } from "@playwright/test";

import manifest from "../lib/demo-codex-assets.json";
import { startIsolatedRedis, unusedNonDefaultPort } from "./helpers/isolated-redis";

const domainApiUrl = process.env.APPSELL_TEST_DOMAIN_API_URL;

test.skipIf(!domainApiUrl)("serves isolated admin and buyer demos with protected PDFs", async () => {
  const generatedFiles = ["next-env.d.ts", "tsconfig.json"];
  const originals = await Promise.all(generatedFiles.map((file) => readFile(join(process.cwd(), file))));
  const redis = await startIsolatedRedis();
  const port = await unusedNonDefaultPort();
  const suffix = `${process.pid}-${Date.now()}`;
  const distDir = `.next-demo-${suffix}`;
  const origin = `http://localhost:${port}`;
  const server = startBff(port, distDir, {
    APPSELL_REDIS_URL: redis.url,
    APPSELL_REDIS_PREFIX: `appsell:test:demo-${suffix}`,
    APPSELL_SESSION_HMAC_KEY_B64: randomBytes(32).toString("base64"),
    APPSELL_CSRF_HMAC_KEY_B64: randomBytes(32).toString("base64"),
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
    APPSELL_DEMO_ENABLED: "true",
    NEXT_TELEMETRY_DISABLED: "1"
  });
  let browser: Browser | undefined;

  try {
    await waitForBff(server, port);
    const invalid = await fetch(`${origin}/auth/login?persona=attacker`, { redirect: "manual" });
    expect(invalid.status).toBe(400);

    const buyer = await login(origin, "buyer");
    expect(buyer.redirect).toBe(`${origin}/library`);
    const library = await fetch(`${origin}/api/library`, { headers: { cookie: buyer.session } });
    expect(library.status, await library.clone().text()).toBe(200);
    const libraryPayload = await library.json() as {
      items: Array<{ product: { title: string }; assets: Array<{ seed_key: string }> }>;
    };
    expect(libraryPayload.items).toHaveLength(1);
    expect(libraryPayload.items[0]?.product.title).toBe("Codex na prática");
    expect(libraryPayload.items[0]?.assets).toHaveLength(5);

    const firstAsset = manifest[0]!;
    const pdf = await fetch(`${origin}${firstAsset.href}`, { headers: { cookie: buyer.session } });
    expect(pdf.status, await pdf.clone().text()).toBe(200);
    expect(pdf.headers.get("content-type")).toBe("application/pdf");
    expect(pdf.headers.get("cache-control")).toBe("private, no-store");
    expect(pdf.headers.get("x-content-type-options")).toBe("nosniff");
    expect((await pdf.arrayBuffer()).byteLength).toBe(firstAsset.bytes);

    const loadResults = await Promise.all(Array.from({ length: 100 }, async () => {
      const started = performance.now();
      const response = await fetch(`${origin}/api/library`, { headers: { cookie: buyer.session } });
      await response.arrayBuffer();
      return { status: response.status, durationMs: performance.now() - started };
    }));
    expect(loadResults.every((result) => result.status === 200)).toBe(true);
    const p95 = loadResults.map((result) => result.durationMs).sort((left, right) => left - right)[94]!;
    expect(p95).toBeLessThan(10_000);

    const pdfLoad = await Promise.all(Array.from({ length: 100 }, async () => {
      const response = await fetch(`${origin}${firstAsset.href}`, { headers: { cookie: buyer.session } });
      await response.arrayBuffer();
      return response.status;
    }));
    expect(pdfLoad.every((status) => status === 200)).toBe(true);

    const admin = await login(origin, "admin");
    expect(admin.redirect).toBe(`${origin}/dashboard`);
    const adminLibrary = await fetch(`${origin}/api/library`, { headers: { cookie: admin.session } });
    expect(adminLibrary.status, await adminLibrary.clone().text()).toBe(200);
    expect(await adminLibrary.json()).toEqual({ items: [] });
    const forbiddenPdf = await fetch(`${origin}${firstAsset.href}`, { headers: { cookie: admin.session } });
    expect(forbiddenPdf.status).not.toBe(200);

    browser = await chromium.launch();
    const buyerContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const buyerPage = await buyerContext.newPage();
    await buyerPage.goto(origin);
    await buyerPage.getByRole("link", { name: "Entrar como aluno demo" }).click();
    await buyerPage.waitForURL(`${origin}/library`);
    await pwExpect(buyerPage.getByRole("heading", { name: "Minha biblioteca" })).toBeVisible();
    await pwExpect(buyerPage.getByRole("heading", { level: 2, name: "Codex na prática", exact: true })).toBeVisible();
    await pwExpect(buyerPage.getByRole("link", { name: /Abrir material em PDF/ })).toHaveCount(5);
    await pwExpect(buyerPage.getByRole("link", { name: "Produtos" })).toHaveCount(0);
    const buyerNav = buyerPage.getByRole("navigation", { name: "Navegação móvel" });
    const navBox = await buyerNav.getByRole("link", { name: "Minha biblioteca" }).boundingBox();
    expect(navBox?.width ?? 0).toBeGreaterThanOrEqual(360);
    const browserPdf = await buyerPage.request.get(`${origin}${firstAsset.href}`);
    expect(browserPdf.status()).toBe(200);
    expect(browserPdf.headers()["content-type"]).toBe("application/pdf");
    await buyerContext.close();

    const adminContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const adminPage = await adminContext.newPage();
    await adminPage.goto(origin);
    await adminPage.getByRole("link", { name: "Entrar como admin demo" }).click();
    await adminPage.waitForURL(`${origin}/dashboard`);
    await pwExpect(adminPage.getByRole("heading", { name: "Bom dia, creator." })).toBeVisible();
    await pwExpect(adminPage.getByRole("link", { name: "Produtos" }).first()).toBeVisible();
    await adminPage.goto(`${origin}/library`);
    await pwExpect(adminPage.getByText("Nenhum produto ativo está disponível para esta conta.")).toBeVisible();
    expect((await adminPage.request.get(`${origin}${firstAsset.href}`)).status()).not.toBe(200);
    await adminContext.close();
  } finally {
    await browser?.close();
    await stopBff(server);
    await redis.stop();
    await rm(join(process.cwd(), distDir), { recursive: true, force: true });
    await Promise.all(generatedFiles.map((file, index) => writeFile(join(process.cwd(), file), originals[index] ?? "")));
  }
}, 60_000);

async function login(origin: string, persona: "admin" | "buyer") {
  const start = await fetch(`${origin}/auth/login?persona=${persona}`, { redirect: "manual" });
  const transaction = cookiePair(start.headers.get("set-cookie"), "appsell_oauth_tx");
  const authorize = await fetch(requiredHeader(start, "location"), { redirect: "manual" });
  const callback = await fetch(requiredHeader(authorize, "location"), {
    redirect: "manual",
    headers: { cookie: transaction }
  });
  expect(callback.status, await callback.clone().text()).toBe(307);
  return {
    redirect: requiredHeader(callback, "location"),
    session: cookiePair(callback.headers.get("set-cookie"), "appsell_session")
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
      // Next.js is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`BFF ${port} did not start: ${diagnostics}`);
}

function cookiePair(setCookie: string | null, name: string) {
  const match = setCookie?.match(new RegExp(`(?:^|,\\s*)(${name}=[^;]+)`));
  if (!match) throw new Error(`Missing ${name} cookie.`);
  return match[1] ?? "";
}

function requiredHeader(response: Response, name: string) {
  const value = response.headers.get(name);
  if (!value) throw new Error(`Missing ${name} header.`);
  return value;
}

function requiredEnvironment(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`Missing integration test setting: ${name}`);
  return value;
}

async function stopBff(process: ChildProcess) {
  if (process.exitCode !== null) return;
  const exited = new Promise<void>((resolve) => process.once("exit", () => resolve()));
  process.kill("SIGTERM");
  await Promise.race([exited, new Promise<void>((resolve) => setTimeout(resolve, 2_000))]);
  if (process.exitCode === null) process.kill("SIGKILL");
}
