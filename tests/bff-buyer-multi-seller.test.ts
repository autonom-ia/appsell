import { execFile, spawn, type ChildProcess } from "node:child_process";
import { randomBytes, randomUUID } from "node:crypto";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { promisify } from "node:util";

import { chromium, type Browser } from "@playwright/test";
import { expect, test } from "vitest";

import { deriveCsrfToken } from "../lib/csrf";
import { startIsolatedRedis, unusedNonDefaultPort } from "./helpers/isolated-redis";

const execFileAsync = promisify(execFile);
const domainApiUrl = process.env.APPSELL_TEST_DOMAIN_API_URL;
const CREATE_PRODUCT_DRAFT_CSRF_ACTION = "POST:/api/products";

interface MultiSellerFixture {
  schema_version: 1;
  mode: "local_ci";
  buyer_subject: string;
  seller_a: string;
  seller_b: string;
  product_a: string;
  product_b: string;
  entitlement_a: string;
  entitlement_b: string;
  lesson_a: string;
  lesson_b: string;
  asset_a: string;
  asset_b: string;
  other_product_a: string;
  draft_product_a: string;
}

test.skipIf(!domainApiUrl)(
  "keeps one global buyer isolated across products from sellers A and B",
  async () => {
    const fixturePath = requiredEnvironment("APPSELL_MULTI_SELLER_MANIFEST");
    const fixture = JSON.parse(await readFile(fixturePath, "utf8")) as MultiSellerFixture;
    assertFixture(fixture);
    const generatedFiles = ["next-env.d.ts", "tsconfig.json"];
    const originals = await Promise.all(
      generatedFiles.map((file) => readFile(join(process.cwd(), file)))
    );
    const redis = await startIsolatedRedis();
    const port = await unusedNonDefaultPort();
    const suffix = `${process.pid}-${Date.now()}`;
    const distDir = `.next-multi-seller-${suffix}`;
    const origin = `http://localhost:${port}`;
    const csrfKey = randomBytes(32);
    const server = startBff(port, distDir, {
      APPSELL_REDIS_URL: redis.url,
      APPSELL_REDIS_PREFIX: `appsell:test:multi-seller-${suffix}`,
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
      APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON: requiredEnvironment(
        "APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON"
      ),
      APPSELL_SERVICE_CREDENTIAL_B64: requiredEnvironment("APPSELL_SERVICE_CREDENTIAL_B64"),
      APP_ENV: "ci",
      APPSELL_DEMO_ENABLED: "true",
      NEXT_TELEMETRY_DISABLED: "1"
    });
    const started = performance.now();
    let testStatus: "passed" | "failed" = "failed";
    let crossScopeDenials = 0;
    let browser: Browser | undefined;

    try {
      await waitForBff(server, port);
      const buyer = await login(origin, "buyer");
      const admin = await login(origin, "admin");
      const adminProducts = await jsonGet(`${origin}/api/me/products`, admin.session);
      expect(adminProducts).toMatchObject({ status: 200, payload: { items: [] } });
      await expectNotFound(
        `${origin}/api/me/products/${fixture.entitlement_a}/seller-products`,
        admin.session
      );
      crossScopeDenials += 1;
      for (const entitlementId of ["not-a-uuid", fixture.entitlement_a]) {
        const anonymous = await fetch(
          `${origin}/api/me/products/${entitlementId}/seller-products`
        );
        expect(anonymous.status).toBe(401);
        expect(await anonymous.json()).toEqual({ error: "unauthorized" });
      }
      const products = await jsonGet(`${origin}/api/me/products`, buyer.session);
      expect(products.status).toBe(200);
      expect(productPairs(products.payload)).toEqual([
        `${fixture.seller_a}:${fixture.product_a}:${fixture.entitlement_a}`,
        `${fixture.seller_b}:${fixture.product_b}:${fixture.entitlement_b}`
      ].sort());

      const compatibility = await jsonGet(`${origin}/api/library`, buyer.session);
      expect(compatibility.status).toBe(200);
      expect(compatibility.payload).toEqual(products.payload);

      const sellerAStorefront = await jsonGet(
        `${origin}/api/me/products/${fixture.entitlement_a}/seller-products`,
        buyer.session
      );
      expect(sellerAStorefront.status).toBe(200);
      expect(storefrontPairs(sellerAStorefront.payload)).toEqual([
        `${fixture.seller_a}:${fixture.other_product_a}`
      ]);
      const serializedStorefront = JSON.stringify(sellerAStorefront.payload);
      expect(serializedStorefront).not.toContain(fixture.product_a);
      expect(serializedStorefront).not.toContain(fixture.product_b);
      expect(serializedStorefront).not.toContain(fixture.draft_product_a);

      const purchasedTitleA = productTitle(products.payload, fixture.product_a);
      const purchasedTitleB = productTitle(products.payload, fixture.product_b);
      const otherProductTitle = productTitle(sellerAStorefront.payload, fixture.other_product_a);
      browser = await chromium.launch({ headless: true });
      const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
      const page = await context.newPage();
      await page.goto(`${origin}/auth/login?persona=buyer`);
      await page.waitForURL(`${origin}/library`);
      await page.getByRole("heading", { name: purchasedTitleA }).waitFor({ state: "visible" });
      await page.getByRole("heading", { name: purchasedTitleB }).waitFor({ state: "visible" });
      const sellerACard = page.locator("article.library-product-card").filter({
        has: page.getByRole("heading", { name: purchasedTitleA })
      });
      await sellerACard.getByRole("button", { name: /^Ver outros produtos de / }).click();
      const sellerProducts = sellerACard.locator(".seller-product-list");
      await sellerProducts.getByText(otherProductTitle, { exact: true }).waitFor({ state: "visible" });
      expect(await sellerProducts.locator("li").count()).toBe(1);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await browser.close();
      browser = undefined;

      const sellerBStorefront = await jsonGet(
        `${origin}/api/me/products/${fixture.entitlement_b}/seller-products`,
        buyer.session
      );
      expect(sellerBStorefront).toMatchObject({ status: 200, payload: { items: [] } });

      const playerA = await player(
        origin,
        buyer.session,
        fixture.entitlement_a,
        fixture.lesson_a
      );
      const playerB = await player(
        origin,
        buyer.session,
        fixture.entitlement_b,
        fixture.lesson_b
      );
      const initialVersionA = Number(playerA.payload.player.state_version);
      const initialVersionB = Number(playerB.payload.player.state_version);
      expect(Number.isSafeInteger(initialVersionA)).toBe(true);
      expect(Number.isSafeInteger(initialVersionB)).toBe(true);
      expect(playerA.payload.player).toMatchObject({
        seller: { id: fixture.seller_a },
        product: { id: fixture.product_a },
        entitlement_id: fixture.entitlement_a
      });
      expect(playerB.payload.player).toMatchObject({
        seller: { id: fixture.seller_b },
        product: { id: fixture.product_b },
        entitlement_id: fixture.entitlement_b,
        selected_asset: {
          media_type: "video/mp4",
          view_href: `/api/library/items/${fixture.entitlement_b}/assets/${fixture.asset_b}`
        }
      });
      expect(playerA.payload.player).toMatchObject({
        selected_asset: {
          media_type: "application/pdf",
          view_href: `/api/library/items/${fixture.entitlement_a}/assets/${fixture.asset_a}/pages`
        }
      });

      await expectNotFound(
        `${origin}/api/library/items/${fixture.entitlement_a}/lessons/${fixture.lesson_b}`,
        buyer.session
      );
      crossScopeDenials += 1;
      await expectNotFound(
        `${origin}/api/library/items/${fixture.entitlement_b}/lessons/${fixture.lesson_a}`,
        buyer.session
      );
      crossScopeDenials += 1;

      await updateProgress(origin, buyer.session, fixture.entitlement_a, fixture.lesson_a, {
        csrf: playerA.payload.csrf.progress,
        expectedVersion: initialVersionA,
        progress: 25
      });
      await updateProgress(origin, buyer.session, fixture.entitlement_b, fixture.lesson_b, {
        csrf: playerB.payload.csrf.progress,
        expectedVersion: initialVersionB,
        progress: 50
      });
      const updatedA = await player(
        origin,
        buyer.session,
        fixture.entitlement_a,
        fixture.lesson_a
      );
      const updatedB = await player(
        origin,
        buyer.session,
        fixture.entitlement_b,
        fixture.lesson_b
      );
      expect(updatedA.payload.player).toMatchObject({ state_version: initialVersionA + 1 });
      const lessonProgressA = updatedA.payload.player.lesson_progress.find((entry) =>
        isProgressEntry(entry) && entry.lesson_id === fixture.lesson_a
      );
      expect(lessonProgressA).toEqual(expect.objectContaining({ lesson_id: fixture.lesson_a }));
      expect((lessonProgressA as { progress_percent: number }).progress_percent).toBeGreaterThanOrEqual(25);
      expect(updatedB.payload.player).toMatchObject({ state_version: initialVersionB + 1 });
      expect(updatedB.payload.player.lesson_progress).toEqual(expect.arrayContaining([
        expect.objectContaining({ lesson_id: fixture.lesson_b, progress_percent: 50 })
      ]));

      await expectPdfAsset(
        `${origin}/api/library/items/${fixture.entitlement_a}/assets/${fixture.asset_a}`,
        buyer.session,
        200
      );
      await expectVideoAsset(
        `${origin}/api/library/items/${fixture.entitlement_b}/assets/${fixture.asset_b}`,
        buyer.session,
        200
      );
      await expectVideoAsset(
        `${origin}/api/library/items/${fixture.entitlement_a}/assets/${fixture.asset_b}`,
        buyer.session,
        404
      );
      crossScopeDenials += 1;
      await expectPdfAsset(
        `${origin}/api/library/items/${fixture.entitlement_b}/assets/${fixture.asset_a}`,
        buyer.session,
        404
      );
      crossScopeDenials += 1;

      const sellerCrud = await fetch(`${origin}/api/sellers/${fixture.seller_a}/products`, {
        headers: { cookie: buyer.session }
      });
      expect(sellerCrud.status).toBe(403);
      expect(await sellerCrud.json()).toEqual({ error: "demo_buyer_capability_forbidden" });
      crossScopeDenials += 1;
      const forbiddenCreate = await fetch(
        `${origin}/api/sellers/${fixture.seller_a}/products`,
        {
          method: "POST",
          headers: { cookie: buyer.session, origin },
          body: new URLSearchParams({
            _csrf: deriveCsrfToken(
              buyer.session.slice(buyer.session.indexOf("=") + 1),
              CREATE_PRODUCT_DRAFT_CSRF_ACTION,
              csrfKey
            ),
            idempotency_key: randomUUID(),
            slug: "buyer-nao-pode-criar",
            product_title: "Buyer não pode criar",
            product_description: "Apenas prova negativa.",
            course_title: "Curso negado"
          })
        }
      );
      expect(forbiddenCreate.status, await forbiddenCreate.clone().text()).toBe(403);
      crossScopeDenials += 1;

      await revokeSellerA(fixturePath);
      const afterRevocation = await jsonGet(`${origin}/api/me/products`, buyer.session);
      expect(afterRevocation.status).toBe(200);
      expect(productPairs(afterRevocation.payload)).toEqual([
        `${fixture.seller_b}:${fixture.product_b}:${fixture.entitlement_b}`
      ]);
      await expectNotFound(
        `${origin}/api/library/items/${fixture.entitlement_a}/lessons/${fixture.lesson_a}`,
        buyer.session
      );
      crossScopeDenials += 1;
      expect((await player(
        origin,
        buyer.session,
        fixture.entitlement_b,
        fixture.lesson_b
      )).payload.player).toMatchObject({
        seller: { id: fixture.seller_b },
        state_version: initialVersionB + 1
      });
      await expectNotFound(
        `${origin}/api/me/products/${fixture.entitlement_a}/seller-products`,
        buyer.session
      );
      crossScopeDenials += 1;
      await expectPdfAsset(
        `${origin}/api/library/items/${fixture.entitlement_a}/assets/${fixture.asset_a}`,
        buyer.session,
        404
      );
      crossScopeDenials += 1;
      await expectVideoAsset(
        `${origin}/api/library/items/${fixture.entitlement_b}/assets/${fixture.asset_b}`,
        buyer.session,
        200
      );
      await expectProgressStatus(
        origin,
        buyer.session,
        fixture.entitlement_a,
        fixture.lesson_a,
        {
          csrf: updatedA.payload.csrf.progress,
          expectedVersion: initialVersionA + 1,
          progress: 30
        },
        404
      );
      crossScopeDenials += 1;
      await updateProgress(origin, buyer.session, fixture.entitlement_b, fixture.lesson_b, {
        csrf: updatedB.payload.csrf.progress,
        expectedVersion: initialVersionB + 1,
        progress: 75
      });
      expect((await player(
        origin,
        buyer.session,
        fixture.entitlement_b,
        fixture.lesson_b
      )).payload.player).toMatchObject({ state_version: initialVersionB + 2 });
      testStatus = "passed";
    } finally {
      await browser?.close();
      await stopBff(server);
      await redis.stop();
      await rm(join(process.cwd(), distDir), { recursive: true, force: true });
      await Promise.all(
        generatedFiles.map((file, index) =>
          writeFile(join(process.cwd(), file), originals[index] ?? "")
        )
      );
      const reportPath = requiredEnvironment("APPSELL_MULTI_SELLER_REPORT");
      await mkdir(dirname(reportPath), { recursive: true });
      await writeFile(reportPath, `${JSON.stringify({
        schema_version: 1,
        mode: "local_ci",
        status: testStatus,
        production_certified: false,
        buyer_accounts: 1,
        seller_accounts: 2,
        active_entitlements_before_revocation: 2,
        active_entitlements_after_revocation: 1,
        independent_progress_states: 2,
        cross_scope_denials: crossScopeDenials,
        duration_ms: Math.round(performance.now() - started)
      }, null, 2)}\n`, "utf8");
    }
  },
  120_000
);

function assertFixture(value: MultiSellerFixture) {
  expect(value).toMatchObject({ schema_version: 1, mode: "local_ci" });
  for (const [key, item] of Object.entries(value)) {
    if (key.endsWith("_a") || key.endsWith("_b")) expect(item).toMatch(/^[0-9a-f-]{36}$/);
  }
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
  const session = cookiePair(callback.headers.get("set-cookie"), "appsell_session");
  return { session };
}

async function jsonGet(url: string, session: string) {
  const response = await fetch(url, { headers: { cookie: session } });
  return { status: response.status, payload: await response.json() as Record<string, unknown> };
}

function productPairs(payload: Record<string, unknown>) {
  const items = payload.items as Array<{
    entitlement_id: string;
    seller: { id: string };
    product: { id: string };
  }>;
  return items.map((item) =>
    `${item.seller.id}:${item.product.id}:${item.entitlement_id}`
  ).sort();
}

function storefrontPairs(payload: Record<string, unknown>) {
  const items = payload.items as Array<{ seller: { id: string }; product: { id: string } }>;
  return items.map((item) => `${item.seller.id}:${item.product.id}`).sort();
}

function productTitle(payload: Record<string, unknown>, productId: string) {
  const items = payload.items as Array<{ product: { id: string; title: string } }>;
  const title = items.find((item) => item.product.id === productId)?.product.title;
  if (!title) throw new Error(`Missing product ${productId} in payload.`);
  return title;
}

function isProgressEntry(value: unknown): value is { lesson_id: string; progress_percent: number } {
  return Boolean(value) && typeof value === "object" &&
    typeof (value as { lesson_id?: unknown }).lesson_id === "string" &&
    typeof (value as { progress_percent?: unknown }).progress_percent === "number";
}

async function player(origin: string, session: string, entitlement: string, lesson: string) {
  const response = await fetch(
    `${origin}/api/library/items/${entitlement}/lessons/${lesson}`,
    { headers: { cookie: session } }
  );
  expect(response.status, await response.clone().text()).toBe(200);
  return { payload: await response.json() as {
    player: Record<string, unknown> & { lesson_progress: unknown[] };
    csrf: { progress: string };
  } };
}

async function updateProgress(
  origin: string,
  session: string,
  entitlement: string,
  lesson: string,
  input: { csrf: string; expectedVersion: number; progress: number }
) {
  const response = await fetch(
    `${origin}/api/library/items/${entitlement}/lessons/${lesson}/progress`,
    {
      method: "PUT",
      headers: { cookie: session, origin },
      body: new URLSearchParams({
        _csrf: input.csrf,
        idempotency_key: randomUUID(),
        progress_percent: String(input.progress),
        expected_version: String(input.expectedVersion)
      })
    }
  );
  expect(response.status, await response.clone().text()).toBe(200);
}

async function expectProgressStatus(
  origin: string,
  session: string,
  entitlement: string,
  lesson: string,
  input: { csrf: string; expectedVersion: number; progress: number },
  status: 404
) {
  const response = await fetch(
    `${origin}/api/library/items/${entitlement}/lessons/${lesson}/progress`,
    {
      method: "PUT",
      headers: { cookie: session, origin },
      body: new URLSearchParams({
        _csrf: input.csrf,
        idempotency_key: randomUUID(),
        progress_percent: String(input.progress),
        expected_version: String(input.expectedVersion)
      })
    }
  );
  expect(response.status).toBe(status);
  expect(await response.json()).toEqual({ error: "learning_item_unavailable" });
}

async function expectNotFound(url: string, session: string) {
  const response = await fetch(url, { headers: { cookie: session } });
  expect(response.status).toBe(404);
  expect(await response.json()).toEqual({ error: "learning_item_unavailable" });
}

async function expectPdfAsset(url: string, session: string, status: 200 | 404) {
  const response = await fetch(`${url}/pages`, { headers: { cookie: session } });
  expect(response.status).toBe(status);
  if (status === 200) {
    expect(response.headers.get("content-type")).toContain("application/json");
    expect(response.headers.get("cache-control")).toBe("private, no-store, max-age=0");
    expect(await response.json()).toMatchObject({
      asset_id: url.slice(url.lastIndexOf("/") + 1),
      page_count: expect.any(Number)
    });
    const page = await fetch(`${url}/pages/1`, { headers: { cookie: session } });
    expect(page.status).toBe(200);
    expect(page.headers.get("content-type")).toBe("image/png");
    expect(page.headers.get("cache-control")).toBe("private, no-store, max-age=0");
    expect((await page.arrayBuffer()).byteLength).toBeGreaterThan(8);
    const original = await fetch(url, { headers: { cookie: session } });
    expect(original.status).toBe(404);
    expect(await original.json()).toEqual({
      error: "pdf_original_requires_download_permission"
    });
  } else {
    expect(await response.json()).toEqual({ error: "learning_item_unavailable" });
  }
}

async function expectVideoAsset(url: string, session: string, status: 200 | 404) {
  if (status === 404) {
    const response = await fetch(url, {
      headers: { cookie: session, range: "bytes=0-7" }
    });
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: "learning_item_unavailable" });
    return;
  }
  const head = await fetch(url, { method: "HEAD", headers: { cookie: session } });
  expect(head.status).toBe(200);
  expect(head.headers.get("content-type")).toBe("video/mp4");
  expect(head.headers.get("accept-ranges")).toBe("bytes");
  const total = Number(head.headers.get("content-length"));
  expect(Number.isSafeInteger(total)).toBe(true);
  expect(total).toBeGreaterThan(8);
  const range = await fetch(url, {
    headers: { cookie: session, range: "bytes=0-7" }
  });
  expect(range.status).toBe(206);
  expect(range.headers.get("content-type")).toBe("video/mp4");
  expect(range.headers.get("content-range")).toBe(`bytes 0-7/${total}`);
  expect((await range.arrayBuffer()).byteLength).toBe(8);
}

async function revokeSellerA(fixturePath: string) {
  const { stdout } = await execFileAsync(
    "uv",
    [
      "run",
      "--project",
      "apps/api",
      "python",
      "-m",
      "appsell_api.seed_buyer_multi_seller",
      "--action",
      "revoke-a",
      "--confirm",
      "buyer-multi-seller-v1",
      "--output",
      fixturePath
    ],
    { cwd: process.cwd(), env: process.env, timeout: 15_000 }
  );
  expect(JSON.parse(stdout)).toMatchObject({ status: "revoked", mode: "local_ci" });
}

function startBff(port: number, distDir: string, shared: Record<string, string>) {
  return spawn(
    process.execPath,
    [join(process.cwd(), "node_modules/next/dist/bin/next"), "dev", "-H", "127.0.0.1", "-p", String(port)],
    {
      cwd: process.cwd(),
      env: { ...process.env, ...shared, APPSELL_NEXT_DIST_DIR: distDir },
      stdio: ["ignore", "pipe", "pipe"],
      detached: process.platform !== "win32"
    }
  );
}

async function waitForBff(process: ChildProcess, port: number) {
  const deadline = Date.now() + 30_000;
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

function signalBff(child: ChildProcess, signal: NodeJS.Signals) {
  if (child.pid === undefined) return;
  if (process.platform !== "win32") {
    try {
      process.kill(-child.pid, signal);
      return;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
    }
  }
  try {
    child.kill(signal);
  } catch {
    // The process may have exited while cleanup was in progress.
  }
}

async function stopBff(child: ChildProcess) {
  if (child.exitCode !== null) return;
  const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));
  signalBff(child, "SIGTERM");
  await Promise.race([exited, new Promise<void>((resolve) => setTimeout(resolve, 2_000))]);
  if (child.exitCode === null) {
    signalBff(child, "SIGKILL");
    await exited;
  }
}
