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
    APPSELL_TRUST_PROXY_HEADERS: "true",
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
    const storefront = await fetch(`${origin}/api/public/storefronts/autonomia-demo?limit=12`);
    expect(storefront.status, await storefront.clone().text()).toBe(200);
    expect(storefront.headers.get("cache-control")).toBe("no-store, max-age=0");
    expect(storefront.headers.get("cdn-cache-control")).toBe("no-store");
    const storefrontPayload = await storefront.json() as {
      seller: { slug: string; name: string };
      products: Array<{ slug: string; title: string; course_count: number }>;
      next_cursor: string | null;
    };
    expect(storefrontPayload.seller).toEqual({ slug: "autonomia-demo", name: "Autonom.ia Demo" });
    expect(storefrontPayload.products).toHaveLength(1);
    expect(storefrontPayload.products[0]).toMatchObject({
      slug: "codex-na-pratica",
      title: "Codex na prática",
      course_count: 1
    });
    const publicBytes = JSON.stringify(storefrontPayload);
    expect(publicBytes).not.toContain("seller_organization_id");
    expect(publicBytes).not.toContain("snapshot");
    expect(publicBytes).not.toContain("modules");
    expect(publicBytes).not.toContain("lessons");
    expect(publicBytes).not.toContain("assets");
    expect((await fetch(`${origin}/api/public/storefronts/Autonomia-Demo`)).status).toBe(404);
    expect((await fetch(`${origin}/api/public/storefronts/autonomia-demo/products/nao-publicado`)).status).toBe(404);

    const invalid = await fetch(`${origin}/auth/login?persona=attacker`, { redirect: "manual" });
    expect(invalid.status).toBe(400);

    const buyer = await login(origin, "buyer");
    expect(buyer.redirect).toBe(`${origin}/library`);
    const library = await fetch(`${origin}/api/library`, { headers: { cookie: buyer.session } });
    expect(library.status, await library.clone().text()).toBe(200);
    const libraryPayload = await library.json() as {
      items: Array<{
        entitlement_id: string;
        product: { title: string };
      }>;
    };
    expect(libraryPayload.items).toHaveLength(1);
    expect(libraryPayload.items[0]?.product.title).toBe("Codex na prática");
    expect(libraryPayload.items[0]).not.toHaveProperty("snapshot");
    expect(libraryPayload.items[0]).not.toHaveProperty("assets");

    const firstAsset = manifest[0]!;
    const entitlementId = libraryPayload.items[0]!.entitlement_id;
    const player = await fetch(`${origin}/api/library/items/${entitlementId}`, {
      headers: { cookie: buyer.session }
    });
    expect(player.status, await player.clone().text()).toBe(200);
    const playerPayload = await player.json() as {
      player: { selected_asset: { href: string } };
    };
    const assetHref = playerPayload.player.selected_asset.href;
    expect(assetHref).toMatch(new RegExp(
      `^/api/library/items/${entitlementId}/assets/[0-9a-f-]{36}$`
    ));
    const pdf = await fetch(`${origin}${assetHref}`, { headers: { cookie: buyer.session } });
    expect(pdf.status, await pdf.clone().text()).toBe(200);
    expect(pdf.headers.get("content-type")).toBe("application/pdf");
    expect(pdf.headers.get("cache-control")).toBe("private, no-store, max-age=0");
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
      const response = await fetch(`${origin}${assetHref}`, { headers: { cookie: buyer.session } });
      await response.arrayBuffer();
      return response.status;
    }));
    expect(pdfLoad.every((status) => status === 200)).toBe(true);

    const admin = await login(origin, "admin");
    expect(admin.redirect).toBe(`${origin}/dashboard`);
    const adminLibrary = await fetch(`${origin}/api/library`, { headers: { cookie: admin.session } });
    expect(adminLibrary.status, await adminLibrary.clone().text()).toBe(200);
    expect(await adminLibrary.json()).toEqual({ items: [] });
    const forbiddenPdf = await fetch(`${origin}${assetHref}`, { headers: { cookie: admin.session } });
    expect(forbiddenPdf.status).not.toBe(200);

    browser = await chromium.launch();
    const publicContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const publicPage = await publicContext.newPage();
    await publicPage.goto(`${origin}/s/autonomia-demo`);
    await pwExpect(publicPage.getByRole("heading", { level: 1, name: "Autonom.ia Demo" })).toBeVisible();
    await pwExpect(publicPage.getByRole("heading", { level: 3, name: "Codex na prática" })).toBeVisible();
    await publicPage.getByRole("link", { name: "Conhecer produto" }).click();
    await publicPage.waitForURL(`${origin}/s/autonomia-demo/codex-na-pratica`);
    await pwExpect(publicPage.getByRole("heading", { level: 1, name: "Codex na prática" })).toBeVisible();
    await pwExpect(publicPage.getByRole("heading", { level: 2, name: "O que você vai encontrar" })).toBeVisible();
    expect(await publicPage.locator("body").innerText()).not.toContain("SEGREDO-DA-AULA-NUNCA-PUBLICAR");
    for (const width of [320, 390, 768, 1024]) {
      await publicPage.setViewportSize({ width, height: 844 });
      await publicPage.goto(`${origin}/s/autonomia-demo`);
      const layout = await publicPage.evaluate(() => ({
        client: document.documentElement.clientWidth,
        scroll: document.documentElement.scrollWidth,
        shortTargets: [...document.querySelectorAll("a,button")].flatMap((target) => {
          const rect = target.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0 && (rect.width < 44 || rect.height < 44)
            ? [{ name: target.textContent?.trim(), width: rect.width, height: rect.height }]
            : [];
        })
      }));
      expect(layout.scroll, `public overflow at ${width}px`).toBeLessThanOrEqual(layout.client);
      expect(layout.shortTargets, `public short targets at ${width}px`).toEqual([]);
    }
    await publicPage.setViewportSize({ width: 320, height: 844 });
    await publicPage.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
    const zoomed = await publicPage.evaluate(() => ({
      client: document.documentElement.clientWidth,
      scroll: document.documentElement.scrollWidth
    }));
    expect(zoomed.scroll).toBeLessThanOrEqual(zoomed.client);
    await publicPage.evaluate(() => { document.documentElement.style.fontSize = "100%"; });
    await publicPage.emulateMedia({ reducedMotion: "reduce" });
    await publicPage.goto(`${origin}/s/autonomia-demo`);
    await publicPage.keyboard.press("Tab");
    await pwExpect(publicPage.getByRole("link", { name: "Pular para o conteúdo" })).toBeFocused();
    await publicPage.keyboard.press("Enter");
    await pwExpect(publicPage.locator("#conteudo-vitrine")).toBeFocused();
    await publicPage.goto(`${origin}/s/autonomia-demo/codex-na-pratica`);
    await publicPage.getByRole("link", { name: "Entrar como aluno demo" }).click();
    await publicPage.waitForURL(`${origin}/library`);
    await pwExpect(publicPage.getByRole("heading", { name: "Minha biblioteca" })).toBeVisible();
    await publicContext.close();

    const buyerContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const buyerPage = await buyerContext.newPage();
    await buyerPage.goto(origin);
    await buyerPage.getByRole("link", { name: "Entrar como aluno demo" }).click();
    await buyerPage.waitForURL(`${origin}/library`);
    await pwExpect(buyerPage.getByRole("heading", { name: "Minha biblioteca" })).toBeVisible();
    await pwExpect(buyerPage.getByRole("heading", { level: 2, name: "Codex na prática", exact: true })).toBeVisible();
    await buyerPage.getByRole("link", { name: "Ver produto e catálogo de Autonom.ia Demo" }).click();
    await buyerPage.waitForURL(`${origin}/s/autonomia-demo/codex-na-pratica`);
    await pwExpect(buyerPage.getByRole("heading", { level: 1, name: "Codex na prática" })).toBeVisible();
    await buyerPage.goto(`${origin}/library`);
    await buyerPage.getByRole("link", { name: "Começar curso" }).click();
    await buyerPage.waitForURL(/\/library\/items\/[0-9a-f-]+\/lessons\/[0-9a-f-]+$/);
    await pwExpect(buyerPage.getByRole("heading", { level: 1, name: "O que é Codex e quando usar" })).toBeVisible();
    await pwExpect(buyerPage.getByRole("link", { name: /Abrir PDF/ })).toHaveCount(1);
    await buyerPage.getByRole("button", { name: "Concluir e avançar" }).click();
    await pwExpect(buyerPage.getByRole("heading", { level: 1, name: "Como preparar contexto, repo e objetivo" })).toBeVisible();
    await buyerPage.goto(`${origin}/library`);
    await buyerPage.getByRole("link", { name: "Continuar curso" }).click();
    await pwExpect(buyerPage.getByRole("heading", { level: 1, name: "Como preparar contexto, repo e objetivo" })).toBeVisible();
    await pwExpect(buyerPage.getByRole("link", { name: "Produtos" })).toHaveCount(0);
    const backBox = await buyerPage.getByRole("link", { name: "Minha biblioteca" }).boundingBox();
    expect(backBox?.height ?? 0).toBeGreaterThanOrEqual(44);
    const browserPdf = await buyerPage.request.get(`${origin}${assetHref}`);
    expect(browserPdf.status()).toBe(200);
    expect(browserPdf.headers()["content-type"]).toBe("application/pdf");
    await buyerContext.close();

    const adminContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const adminPage = await adminContext.newPage();
    await adminPage.goto(origin);
    await adminPage.getByRole("link", { name: "Entrar como admin demo" }).click();
    await adminPage.waitForURL(`${origin}/dashboard`);
    await pwExpect(adminPage.getByRole("heading", { name: "Seu negócio em um só lugar." })).toBeVisible();
    await pwExpect(adminPage.getByRole("link", { name: "Produtos" }).first()).toBeVisible();
    await adminPage.goto(`${origin}/library`);
    await pwExpect(adminPage.getByText("Nenhum produto ativo está disponível para esta conta.")).toBeVisible();
    expect((await adminPage.request.get(`${origin}${assetHref}`)).status()).not.toBe(200);
    await adminContext.close();

    const unavailable = await fetch(`${origin}/storefront-unavailable?seller=autonomia-demo`);
    expect(unavailable.status).toBe(503);
    expect(unavailable.headers.get("retry-after")).toBe("30");
    expect(await unavailable.text()).toContain("<h1>Não foi possível carregar esta vitrine.</h1>");

    const rateHeaders = { "x-forwarded-for": "203.0.113.200" };
    for (let index = 0; index < 60; index += 1) {
      const allowed = await fetch(`${origin}/api/public/storefronts/autonomia-demo?limit=1`, {
        headers: rateHeaders
      });
      expect(allowed.status, `rate request ${index + 1}`).toBe(200);
    }
    const limited = await fetch(`${origin}/s/autonomia-demo`, { headers: rateHeaders });
    expect(limited.status).toBe(429);
    expect(limited.headers.get("retry-after")).toBe("60");
    const limitedHtml = await limited.text();
    expect(limitedHtml).toContain("<main>");
    expect(limitedHtml).toContain("<h1>A vitrine precisa de um instante.</h1>");
    expect(limitedHtml).toContain("Tentar novamente");

    await redis.stopServer();
    const protectedFailure = await fetch(`${origin}/s/autonomia-demo`, {
      headers: { "x-forwarded-for": "203.0.113.201" }
    });
    expect(protectedFailure.status).toBe(503);
    const protectedFailureHtml = await protectedFailure.text();
    expect(protectedFailureHtml).toContain("<h1>Não foi possível carregar esta vitrine.</h1>");
    expect(protectedFailureHtml).toContain("Ir para o início");

    await redis.restartServer();
    const recovered = await waitForStorefrontRecovery(origin, server);
    expect(recovered.status, await recovered.clone().text()).toBe(200);
  } finally {
    await browser?.close();
    await stopBff(server);
    await redis.stop();
    await rm(join(process.cwd(), distDir), { recursive: true, force: true });
    await Promise.all(generatedFiles.map((file, index) => writeFile(join(process.cwd(), file), originals[index] ?? "")));
  }
}, 60_000);

async function waitForStorefrontRecovery(origin: string, server: ChildProcess) {
  const deadline = Date.now() + 5_000;
  let response: Response | undefined;
  while (Date.now() < deadline) {
    if (server.exitCode !== null) throw new Error("BFF exited during Redis recovery.");
    response = await fetch(`${origin}/api/public/storefronts/autonomia-demo?limit=1`, {
      headers: { "x-forwarded-for": "203.0.113.202" }
    });
    if (response.status === 200) return response;
    expect(response.status).toBe(503);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`BFF did not recover after Redis restart; last status ${response?.status ?? "none"}.`);
}

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
