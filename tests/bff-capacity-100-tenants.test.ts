import { execFile, spawn, type ChildProcess } from "node:child_process";
import { createHash, randomBytes, randomUUID } from "node:crypto";
import { access, readFile, rm, writeFile } from "node:fs/promises";
import { cpus, freemem, platform, arch, release, totalmem } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";

import { test } from "vitest";

import { RedisOAuthService } from "../lib/redis-oauth-service";
import { startIsolatedRedis, unusedNonDefaultPort } from "./helpers/isolated-redis";

const execFileAsync = promisify(execFile);
const domainApiUrl = process.env.APPSELL_TEST_DOMAIN_API_URL;
const CAPACITY = 100;
const CONCURRENCY = 10;

interface FixtureTenant {
  ordinal: number;
  owner_subject: string;
  buyer_subject: string;
  seller_id: string;
  seller_slug: string;
  product_id: string;
  product_slug: string;
  product_title: string;
  entitlement_id: string;
  asset_id: string;
  asset_sha256: string;
}

interface CapacitySession {
  sessionCookie: string;
  cookieHeader: string;
  productCsrf: string;
}

interface FixtureManifest {
  schema_version: number;
  mode: string;
  tenant_count: number;
  distinct_subjects: number;
  tenants: FixtureTenant[];
}

interface ScenarioResult {
  requests: number;
  successes: number;
  expected_denials: number;
  unexpected_errors: number;
  validation_failures: number;
  timeouts: number;
  diagnostics: string[];
  statuses: Record<string, number>;
  duration_ms: number;
  throughput_per_second: number;
  latency_ms: { p50: number; p95: number; p99: number; max: number };
}

interface ResourcePeaks {
  bff_rss_bytes: number;
  redis_rss_bytes: number;
  container_rss_bytes: Record<string, number>;
  postgres_connections: number;
  postgres_active_connections: number;
  postgres_idle_connections: number;
  postgres_active_waiting: number;
  postgres_samples_with_active_waiting: number;
  postgres_active_waiting_nominal_observed_ms: number;
  samples: number;
}

test.skipIf(!domainApiUrl)("certifies a Local/CI baseline for 100 isolated tenants", async () => {
  const manifest = JSON.parse(
    await readFile(requiredEnvironment("APPSELL_CAPACITY_MANIFEST"), "utf8")
  ) as FixtureManifest;
  assertFixture(manifest);

  const generatedFiles = ["next-env.d.ts", "tsconfig.json"];
  const originals = await Promise.all(
    generatedFiles.map((file) => readFile(join(process.cwd(), file)))
  );
  const containers = JSON.parse(
    requiredEnvironment("APPSELL_CAPACITY_CONTAINER_IDS_JSON")
  ) as Record<string, string>;
  let redis: Awaited<ReturnType<typeof startIsolatedRedis>> | undefined;
  let bff: ChildProcess | undefined;
  let oauth: RedisOAuthService | undefined;
  let sampler: ReturnType<typeof startResourceSampler> | undefined;
  let port = 0;
  const suffix = `${process.pid}-${Date.now()}`;
  const distDir = `.next-capacity-${suffix}`;
  const scenarios: Record<string, ScenarioResult> = {};
  let testFailure: unknown;
  let peaks = emptyResourcePeaks(containers);
  const cleanupErrors: string[] = [];
  let redisKeysBeforeShutdown = 0;
  let warmupDurationMs = 0;

  try {
    redis = await startIsolatedRedis();
    port = await unusedNonDefaultPort();
    const shared = capacityEnvironment(redis.url, port, suffix);
    bff = startBff(port, distDir, shared);
    const activeOauth = RedisOAuthService.fromEnvironment(shared);
    oauth = activeOauth;
    sampler = startResourceSampler(bff.pid!, redis.pid, containers);
    await waitForBff(bff, port);

    // Warm compilation, OAuth callback, domain sync and both read route families.
    const warmupStarted = performance.now();
    const warmOwner = await createSession(activeOauth, port, manifest.tenants[0]!.owner_subject, "warm-owner");
    const warmBuyer = await createSession(activeOauth, port, manifest.tenants[0]!.buyer_subject, "warm-buyer");
    await requireStatus(
      timedFetch(`${origin(port)}/api/sellers/${manifest.tenants[0]!.seller_id}/products`, {
        headers: { cookie: warmOwner.sessionCookie }
      }),
      200,
      "warm owner catalog"
    );
    await requireStatus(
      timedFetch(`${origin(port)}/api/library`, { headers: { cookie: warmBuyer.sessionCookie } }),
      200,
      "warm buyer library"
    );
    await requireStatus(
      timedFetch(
        `${origin(port)}/api/sellers/${manifest.tenants[1]!.seller_id}/products/${manifest.tenants[1]!.product_id}`,
        {
          headers: { cookie: warmOwner.sessionCookie }
        }
      ),
      404,
      "warm cross-tenant denial"
    );
    const warmProduct = await timedFetch(
      `${origin(port)}/api/sellers/${manifest.tenants[0]!.seller_id}/products`,
      {
        method: "POST",
        headers: {
          cookie: warmOwner.cookieHeader,
          origin: `http://localhost:${port}`
        },
        body: new URLSearchParams({
          _csrf: warmOwner.productCsrf,
          idempotency_key: randomUUID(),
          slug: "capacity-warm-product",
          product_title: "Produto de warmup",
          product_description: "Fora das métricas.",
          course_title: "Curso de warmup"
        })
      }
    );
    if (warmProduct.status !== 201) {
      throw new Error(`warm product create expected 201, got ${warmProduct.status}`);
    }
    const warmProductPayload = await warmProduct.json() as { product_id?: string };
    if (typeof warmProductPayload.product_id !== "string") {
      throw new Error("warm product create did not return a product id");
    }
    const warmProductId = warmProductPayload.product_id;
    const warmAsset = await timedFetch(
      `${origin(port)}/api/library/items/${manifest.tenants[0]!.entitlement_id}/assets/${manifest.tenants[0]!.asset_id}`,
      { headers: { cookie: warmBuyer.sessionCookie } }
    );
    if (warmAsset.status !== 200 || (await warmAsset.arrayBuffer()).byteLength === 0) {
      throw new Error(`warm own asset expected non-empty 200, got ${warmAsset.status}`);
    }
    await requireStatus(
      timedFetch(
        `${origin(port)}/api/library/items/${manifest.tenants[1]!.entitlement_id}/assets/${manifest.tenants[1]!.asset_id}`,
        { headers: { cookie: warmBuyer.sessionCookie } }
      ),
      404,
      "warm cross-tenant asset denial"
    );
    warmupDurationMs = round(performance.now() - warmupStarted);

    const ownerSessions = new Map<number, CapacitySession>();
    const buyerSessions = new Map<number, CapacitySession>();
    scenarios.oauth_callback_and_sync = await measureScenario(
      manifest.tenants.flatMap((tenant) => [
        { tenant, role: "owner" as const, subject: tenant.owner_subject },
        { tenant, role: "buyer" as const, subject: tenant.buyer_subject }
      ]),
      async ({ tenant, role, subject }) => {
        const cookie = await createSession(
          activeOauth,
          port,
          subject,
          `capacity-${role}-${tenant.ordinal.toString().padStart(3, "0")}`
        );
        (role === "owner" ? ownerSessions : buyerSessions).set(tenant.ordinal, cookie);
        return { status: 307, expected: 307 };
      }
    );
    requireThreshold("oauth_callback_and_sync", scenarios.oauth_callback_and_sync, {
      p95: 1_200,
      p99: 2_000,
      max: 3_000,
      throughput: 8
    });
    requireScenarioClean("oauth_callback_and_sync", scenarios.oauth_callback_and_sync);

    scenarios.owner_catalog = await measureScenario(manifest.tenants, async (tenant) => {
      const response = await timedFetch(`${origin(port)}/api/sellers/${tenant.seller_id}/products`, {
        headers: { cookie: requiredSession(ownerSessions, tenant.ordinal, "owner").sessionCookie }
      });
      const payload = await response.clone().json() as {
        products?: Array<{ id?: string; slug?: string; title?: string }>;
      };
      const expectedIds = tenant.ordinal === 0
        ? new Set([tenant.product_id, warmProductId])
        : new Set([tenant.product_id]);
      const actualIds = new Set(payload.products?.map((product) => product.id) ?? []);
      const exactSet = actualIds.size === expectedIds.size &&
        [...actualIds].every((id) => typeof id === "string" && expectedIds.has(id));
      const own = exactSet && payload.products?.some((product) =>
        product.id === tenant.product_id && product.slug === tenant.product_slug &&
        product.title === tenant.product_title
      ) === true;
      return { status: response.status, expected: 200, valid: own };
    });
    requireThreshold("owner_catalog", scenarios.owner_catalog, {
      p95: 750,
      p99: 1_200,
      max: 2_500,
      throughput: 10
    });
    requireScenarioClean("owner_catalog", scenarios.owner_catalog);

    const createdProducts = new Map<number, { productId: string; title: string }>();
    scenarios.product_create = await measureScenario(manifest.tenants, async (tenant) => {
      const session = requiredSession(ownerSessions, tenant.ordinal, "owner");
      const response = await timedFetch(
        `${origin(port)}/api/sellers/${tenant.seller_id}/products`,
        {
          method: "POST",
          headers: {
            cookie: session.cookieHeader,
            origin: `http://localhost:${port}`
          },
          body: new URLSearchParams({
            _csrf: session.productCsrf,
            idempotency_key: randomUUID(),
            slug: `capacity-write-${tenant.ordinal.toString().padStart(3, "0")}`,
            product_title: `Produto escrito ${tenant.ordinal.toString().padStart(3, "0")}`,
            product_description: "Mutação real do certificado Local/CI.",
            course_title: `Curso escrito ${tenant.ordinal.toString().padStart(3, "0")}`
          })
        }
      );
      const payload = await response.clone().json() as {
        product_id?: string;
        course_id?: string;
        status?: string;
        created?: boolean;
      };
      const valid = payload.created === true && payload.status === "draft" &&
        typeof payload.product_id === "string" && typeof payload.course_id === "string";
      if (valid) {
        createdProducts.set(tenant.ordinal, {
          productId: payload.product_id!,
          title: `Produto escrito ${tenant.ordinal.toString().padStart(3, "0")}`
        });
      }
      return { status: response.status, expected: 201, valid };
    });
    requireThreshold("product_create", scenarios.product_create, {
      p95: 1_200,
      p99: 2_000,
      max: 3_000,
      throughput: 8
    });
    requireScenarioClean("product_create", scenarios.product_create);

    scenarios.product_create_verify = await measureScenario(manifest.tenants, async (tenant) => {
      const created = createdProducts.get(tenant.ordinal);
      if (!created) throw new Error(`missing created product for tenant ${tenant.ordinal}`);
      const response = await timedFetch(
        `${origin(port)}/api/sellers/${tenant.seller_id}/products/${created.productId}`,
        {
          headers: {
            cookie: requiredSession(ownerSessions, tenant.ordinal, "owner").sessionCookie
          }
        }
      );
      const payload = await response.clone().json() as {
        product?: { id?: string; seller_organization_id?: string; title?: string };
      };
      const valid = payload.product?.id === created.productId &&
        payload.product?.seller_organization_id === tenant.seller_id &&
        payload.product?.title === created.title;
      return { status: response.status, expected: 200, valid };
    });
    requireThreshold("product_create_verify", scenarios.product_create_verify, {
      p95: 750,
      p99: 1_200,
      max: 2_500,
      throughput: 10
    });
    requireScenarioClean("product_create_verify", scenarios.product_create_verify);

    scenarios.buyer_library = await measureScenario(manifest.tenants, async (tenant) => {
      const response = await timedFetch(`${origin(port)}/api/library`, {
        headers: { cookie: requiredSession(buyerSessions, tenant.ordinal, "buyer").sessionCookie }
      });
      const payload = await response.clone().json() as {
        items?: Array<{ entitlement_id?: string; product?: { id?: string; title?: string } }>;
      };
      const serialized = JSON.stringify(payload);
      const next = manifest.tenants[(tenant.ordinal + 1) % CAPACITY]!;
      const own = payload.items?.length === 1 &&
        payload.items[0]?.entitlement_id === tenant.entitlement_id &&
        payload.items[0]?.product?.id === tenant.product_id &&
        payload.items[0]?.product?.title === tenant.product_title &&
        !serialized.includes(next.product_id) && !serialized.includes(next.product_title);
      return { status: response.status, expected: 200, valid: own };
    });
    requireThreshold("buyer_library", scenarios.buyer_library, {
      p95: 750,
      p99: 1_200,
      max: 2_500,
      throughput: 10
    });
    requireScenarioClean("buyer_library", scenarios.buyer_library);

    scenarios.buyer_asset = await measureScenario(manifest.tenants, async (tenant) => {
      const response = await timedFetch(
        `${origin(port)}/api/library/items/${tenant.entitlement_id}/assets/${tenant.asset_id}`,
        {
          headers: {
            cookie: requiredSession(buyerSessions, tenant.ordinal, "buyer").sessionCookie
          }
        }
      );
      const payload = response.status === 200 ? await response.arrayBuffer() : new ArrayBuffer(0);
      const valid = response.headers.get("content-type") === "application/pdf" &&
        payload.byteLength > 0 &&
        createHash("sha256").update(new Uint8Array(payload)).digest("hex") === tenant.asset_sha256;
      return { status: response.status, expected: 200, valid };
    });
    requireThreshold("buyer_asset", scenarios.buyer_asset, {
      p95: 1_200,
      p99: 2_000,
      max: 3_000,
      throughput: 8
    });
    requireScenarioClean("buyer_asset", scenarios.buyer_asset);

    scenarios.cross_tenant_denial = await measureScenario(manifest.tenants, async (tenant) => {
      const target = manifest.tenants[(tenant.ordinal + 1) % CAPACITY]!;
      const response = await timedFetch(
        `${origin(port)}/api/sellers/${target.seller_id}/products/${target.product_id}`,
        {
          headers: {
            cookie: requiredSession(ownerSessions, tenant.ordinal, "owner").sessionCookie
          }
        }
      );
      return { status: response.status, expected: 404 };
    });
    requireThreshold("cross_tenant_denial", scenarios.cross_tenant_denial, {
      p95: 750,
      p99: 1_200,
      max: 2_500,
      throughput: 10
    });
    requireScenarioClean("cross_tenant_denial", scenarios.cross_tenant_denial);
    if (scenarios.cross_tenant_denial.expected_denials !== CAPACITY) {
      throw new Error("cross-tenant denial budget was not 100/100");
    }

    scenarios.cross_tenant_asset_denial = await measureScenario(
      manifest.tenants,
      async (tenant) => {
        const target = manifest.tenants[(tenant.ordinal + 1) % CAPACITY]!;
        const response = await timedFetch(
          `${origin(port)}/api/library/items/${target.entitlement_id}/assets/${target.asset_id}`,
          {
            headers: {
              cookie: requiredSession(buyerSessions, tenant.ordinal, "buyer").sessionCookie
            }
          }
        );
        return { status: response.status, expected: 404 };
      }
    );
    requireThreshold("cross_tenant_asset_denial", scenarios.cross_tenant_asset_denial, {
      p95: 750,
      p99: 1_200,
      max: 2_500,
      throughput: 10
    });
    requireScenarioClean("cross_tenant_asset_denial", scenarios.cross_tenant_asset_denial);
    if (scenarios.cross_tenant_asset_denial.expected_denials !== CAPACITY) {
      throw new Error("cross-tenant asset denial budget was not 100/100");
    }
  } catch (error) {
    testFailure = error;
  } finally {
    if (sampler) {
      await cleanupStep(cleanupErrors, "resource sampler", async () => {
        peaks = await withTimeout(sampler!.stop(), 10_000, "resource sampler stop");
      });
    }
    if (redis?.client.status === "ready") {
      await cleanupStep(cleanupErrors, "Redis key count", async () => {
        redisKeysBeforeShutdown = (await redis!.client.keys(`appsell:test:capacity-${suffix}:*`)).length;
      });
    }
    await cleanupStep(cleanupErrors, "OAuth close", async () => { await oauth?.close(); });
    await cleanupStep(cleanupErrors, "BFF stop", async () => {
      if (bff) await withTimeout(stopBff(bff), 5_000, "BFF stop");
    });
    await cleanupStep(cleanupErrors, "Redis stop", async () => {
      if (redis) await withTimeout(redis.stop(), 5_000, "Redis stop");
    });
    await cleanupStep(cleanupErrors, "Next dist cleanup", async () => {
      await rm(join(process.cwd(), distDir), { recursive: true, force: true });
    });
    await cleanupStep(cleanupErrors, "generated config restore", async () => {
      await Promise.all(
        generatedFiles.map((file, index) =>
          writeFile(join(process.cwd(), file), originals[index] ?? "")
        )
      );
    });
    const runtimeTeardown = {
      bff_process_stopped: bff?.pid ? !processExists(bff.pid) : true,
      redis_process_stopped: redis ? !processExists(redis.pid) : true,
      redis_keys_destroyed_with_ephemeral_instance: redisKeysBeforeShutdown,
      next_dist_removed: !(await pathExists(join(process.cwd(), distDir))),
      cleanup_errors: cleanupErrors
    };
    if (cleanupErrors.length > 0 && !testFailure) {
      testFailure = new Error(`runtime cleanup failed: ${cleanupErrors.join(" | ")}`);
    }
    if (!testFailure) {
      try {
        requireResourceThresholds(peaks);
      } catch (error) {
        testFailure = error;
      }
    }
    const report = await buildReport(
      manifest,
      scenarios,
      peaks,
      containers,
      runtimeTeardown,
      warmupDurationMs,
      testFailure
    );
    await writeCapacityReport(report);
  }

  if (testFailure) throw testFailure;
}, 240_000);

function capacityEnvironment(redisUrl: string, port: number, suffix: string) {
  return {
    APPSELL_REDIS_URL: redisUrl,
    APPSELL_REDIS_PREFIX: `appsell:test:capacity-${suffix}`,
    APPSELL_SESSION_HMAC_KEY_B64: randomBytes(32).toString("base64"),
    APPSELL_CSRF_HMAC_KEY_B64: randomBytes(32).toString("base64"),
    APPSELL_PUBLIC_ORIGIN: `http://localhost:${port}`,
    APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: JSON.stringify([
      { id: "capacity-v1", key: randomBytes(32).toString("base64"), active: true }
    ]),
    APPSELL_DOMAIN_API_URL: requiredEnvironment("APPSELL_TEST_DOMAIN_API_URL"),
    APPSELL_INTERNAL_ISSUER: requiredEnvironment("APPSELL_INTERNAL_ISSUER"),
    APPSELL_INTERNAL_AUDIENCE: requiredEnvironment("APPSELL_INTERNAL_AUDIENCE"),
    APPSELL_IDENTITY_ISSUER: requiredEnvironment("APPSELL_IDENTITY_ISSUER"),
    APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON: requiredEnvironment(
      "APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON"
    ),
    APPSELL_SERVICE_CREDENTIAL_B64: requiredEnvironment("APPSELL_SERVICE_CREDENTIAL_B64"),
    APPSELL_LOCAL_IDENTITY_EMAIL: "capacity.synthetic@autonomia.site",
    APPSELL_DEMO_ENABLED: "false",
    NEXT_TELEMETRY_DISABLED: "1"
  };
}

async function createSession(
  oauth: RedisOAuthService,
  port: number,
  subject: string,
  clientKey: string
) {
  const login = await oauth.beginLogin(clientKey);
  const code = await oauth.authorize(
    { state: login.state, nonce: login.nonce, codeChallenge: login.codeChallenge },
    clientKey,
    subject
  );
  const response = await timedFetch(
    `${origin(port)}/auth/callback?code=${encodeURIComponent(code)}&state=${encodeURIComponent(login.state)}`,
    {
      redirect: "manual",
      headers: { cookie: `appsell_oauth_tx=${login.transactionId}` }
    }
  );
  if (response.status !== 307) {
    throw new Error(`OAuth callback for ${subject} returned ${response.status}: ${await response.text()}`);
  }
  const setCookie = response.headers.get("set-cookie");
  const sessionCookie = cookiePair(setCookie, "appsell_session");
  const productCsrfCookie = cookiePair(setCookie, "appsell_product_csrf");
  return {
    sessionCookie,
    cookieHeader: `${sessionCookie}; ${productCsrfCookie}`,
    productCsrf: productCsrfCookie.slice(productCsrfCookie.indexOf("=") + 1)
  };
}

async function measureScenario<T>(
  items: T[],
  operation: (item: T) => Promise<{ status: number; expected: number; valid?: boolean }>
): Promise<ScenarioResult> {
  const latencies: number[] = [];
  const statuses: Record<string, number> = {};
  let successes = 0;
  let expectedDenials = 0;
  let unexpectedErrors = 0;
  let validationFailures = 0;
  let timeouts = 0;
  let cursor = 0;
  const started = performance.now();
  const errors: string[] = [];
  await Promise.all(
    Array.from({ length: Math.min(CONCURRENCY, items.length) }, async () => {
      while (true) {
        const index = cursor++;
        if (index >= items.length) return;
        const itemStarted = performance.now();
        try {
          const result = await operation(items[index]!);
          latencies.push(performance.now() - itemStarted);
          statuses[String(result.status)] = (statuses[String(result.status)] ?? 0) + 1;
          if (result.status === result.expected && result.valid !== false) {
            successes += 1;
            if (result.expected === 403 || result.expected === 404) expectedDenials += 1;
          } else {
            unexpectedErrors += 1;
            if (result.valid === false) validationFailures += 1;
            errors.push(`request ${index}: expected ${result.expected}, got ${result.status}, valid=${result.valid}`);
          }
        } catch (error) {
          latencies.push(performance.now() - itemStarted);
          unexpectedErrors += 1;
          if (/timeout|timed out|abort/i.test(safeError(error))) timeouts += 1;
          errors.push(`request ${index}: ${safeError(error)}`);
        }
      }
    })
  );
  const duration = performance.now() - started;
  const result: ScenarioResult = {
    requests: items.length,
    successes,
    expected_denials: expectedDenials,
    unexpected_errors: unexpectedErrors,
    validation_failures: validationFailures,
    timeouts,
    diagnostics: errors.slice(0, 5),
    statuses,
    duration_ms: round(duration),
    throughput_per_second: round(items.length / Math.max(duration / 1_000, 0.001)),
    latency_ms: {
      p50: percentile(latencies, 50),
      p95: percentile(latencies, 95),
      p99: percentile(latencies, 99),
      max: round(Math.max(...latencies, 0))
    }
  };
  return result;
}

function requireScenarioClean(name: string, result: ScenarioResult) {
  if (result.unexpected_errors > 0) {
    throw new Error(`${name} failed with ${result.unexpected_errors} unexpected errors`);
  }
}

function requireThreshold(
  name: string,
  result: ScenarioResult,
  threshold: { p95: number; p99: number; max: number; throughput: number }
) {
  const failures = [
    result.latency_ms.p95 > threshold.p95 ? `p95 ${result.latency_ms.p95}>${threshold.p95}` : "",
    result.latency_ms.p99 > threshold.p99 ? `p99 ${result.latency_ms.p99}>${threshold.p99}` : "",
    result.latency_ms.max > threshold.max ? `max ${result.latency_ms.max}>${threshold.max}` : "",
    result.throughput_per_second < threshold.throughput
      ? `throughput ${result.throughput_per_second}<${threshold.throughput}`
      : ""
  ].filter(Boolean);
  if (failures.length > 0) throw new Error(`${name} threshold failed: ${failures.join(", ")}`);
}

function requireResourceThresholds(resources: ResourcePeaks) {
  const gib = 1024 ** 3;
  const limits: Record<string, [number, number]> = {
    bff: [resources.bff_rss_bytes, 2 * gib],
    redis: [resources.redis_rss_bytes, 128 * 1024 ** 2],
    postgres: [resources.container_rss_bytes.postgres ?? 0, 512 * 1024 ** 2],
    api: [resources.container_rss_bytes.api ?? 0, 512 * 1024 ** 2],
    pdf_sandbox: [resources.container_rss_bytes.pdf_sandbox ?? 0, 768 * 1024 ** 2],
    seaweedfs: [resources.container_rss_bytes.seaweedfs ?? 0, 512 * 1024 ** 2]
  };
  const failures = Object.entries(limits).flatMap(([name, [value, limit]]) => {
    if (value <= 0) return [`${name} RSS was not measured`];
    return value > limit ? [`${name} RSS ${value}>${limit}`] : [];
  });
  const workerRss = Number(requiredEnvironment("APPSELL_CAPACITY_STORAGE_VERTICAL_TREE_RSS_BYTES"));
  if (!Number.isFinite(workerRss) || workerRss <= 0) {
    failures.push("storage vertical process-tree RSS was not measured");
  }
  if (workerRss > gib) failures.push(`storage vertical process-tree RSS ${workerRss}>${gib}`);
  const total = Object.values(limits).reduce((sum, [value]) => sum + value, 0);
  const totalLimit = Math.min(4 * gib, totalmem() * 0.7);
  if (total > totalLimit) failures.push(`total measured RSS ${total}>${totalLimit}`);
  if (resources.samples < 2) failures.push("resource sampler produced fewer than two samples");
  if (resources.postgres_connections <= 0 || resources.postgres_connections > 12) {
    failures.push(`Postgres connections out of baseline: ${resources.postgres_connections}`);
  }
  if (resources.postgres_active_connections > 11) {
    failures.push(`Postgres active connections out of baseline: ${resources.postgres_active_connections}`);
  }
  if (failures.length > 0) throw new Error(`resource threshold failed: ${failures.join(", ")}`);
}

function startResourceSampler(
  bffPid: number,
  redisPid: number,
  containers: Record<string, string>
) {
  let running = true;
  const peaks: ResourcePeaks = {
    bff_rss_bytes: 0,
    redis_rss_bytes: 0,
    container_rss_bytes: Object.fromEntries(Object.keys(containers).map((name) => [name, 0])),
    postgres_connections: 0,
    postgres_active_connections: 0,
    postgres_idle_connections: 0,
    postgres_active_waiting: 0,
    postgres_samples_with_active_waiting: 0,
    postgres_active_waiting_nominal_observed_ms: 0,
    samples: 0
  };
  const loop = (async () => {
    while (running) {
      const [bff, redis, containerSamples, connections] = await Promise.all([
        processTreeRss(bffPid),
        processTreeRss(redisPid),
        Promise.all(
          Object.entries(containers).map(async ([name, id]) => [name, await containerRss(id)] as const)
        ),
        postgresConnections(containers.postgres)
      ]);
      peaks.bff_rss_bytes = Math.max(peaks.bff_rss_bytes, bff);
      peaks.redis_rss_bytes = Math.max(peaks.redis_rss_bytes, redis);
      for (const [name, bytes] of containerSamples) {
        peaks.container_rss_bytes[name] = Math.max(peaks.container_rss_bytes[name] ?? 0, bytes);
      }
      peaks.postgres_connections = Math.max(peaks.postgres_connections, connections.total);
      peaks.postgres_active_connections = Math.max(
        peaks.postgres_active_connections,
        connections.active
      );
      peaks.postgres_idle_connections = Math.max(peaks.postgres_idle_connections, connections.idle);
      peaks.postgres_active_waiting = Math.max(
        peaks.postgres_active_waiting,
        connections.activeWaiting
      );
      if (connections.activeWaiting > 0) {
        peaks.postgres_samples_with_active_waiting += 1;
        peaks.postgres_active_waiting_nominal_observed_ms += 250;
      }
      peaks.samples += 1;
      await delay(250);
    }
  })();
  return {
    stop: async () => {
      running = false;
      await loop;
      return peaks;
    }
  };
}

function emptyResourcePeaks(containers: Record<string, string>): ResourcePeaks {
  return {
    bff_rss_bytes: 0,
    redis_rss_bytes: 0,
    container_rss_bytes: Object.fromEntries(Object.keys(containers).map((name) => [name, 0])),
    postgres_connections: 0,
    postgres_active_connections: 0,
    postgres_idle_connections: 0,
    postgres_active_waiting: 0,
    postgres_samples_with_active_waiting: 0,
    postgres_active_waiting_nominal_observed_ms: 0,
    samples: 0
  };
}

async function cleanupStep(errors: string[], label: string, operation: () => Promise<void>) {
  try {
    await operation();
  } catch (error) {
    errors.push(`${label}: ${safeError(error)}`);
  }
}

async function withTimeout<T>(operation: Promise<T>, timeoutMs: number, label: string) {
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      operation,
      new Promise<never>((_resolve, reject) => {
        timeout = setTimeout(() => reject(new Error(`${label} timed out`)), timeoutMs);
      })
    ]);
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}

function processExists(pid: number) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function pathExists(path: string) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

async function processTreeRss(rootPid: number) {
  try {
    const { stdout } = await execFileAsync(
      "ps",
      ["-axo", "pid=,ppid=,rss="],
      { timeout: 3_000 }
    );
    const rows = stdout.trim().split("\n").map((line) => line.trim().split(/\s+/).map(Number));
    const selected = new Set([rootPid]);
    let changed = true;
    while (changed) {
      changed = false;
      for (const [pid, parent] of rows) {
        if (selected.has(parent!) && !selected.has(pid!)) {
          selected.add(pid!);
          changed = true;
        }
      }
    }
    return rows.reduce((sum, [pid, , rss]) => sum + (selected.has(pid!) ? (rss ?? 0) * 1024 : 0), 0);
  } catch {
    return 0;
  }
}

async function containerRss(containerId: string | undefined) {
  if (!containerId) return 0;
  try {
    const { stdout } = await execFileAsync(
      "docker",
      ["top", containerId, "-eo", "rss,pid,ppid,comm"],
      { timeout: 3_000 }
    );
    return stdout.trim().split("\n").slice(1).reduce((sum, line) => {
      const rss = Number(line.trim().split(/\s+/)[0]);
      return sum + (Number.isFinite(rss) ? rss * 1024 : 0);
    }, 0);
  } catch {
    return 0;
  }
}

async function postgresConnections(containerId: string | undefined) {
  if (!containerId) return { total: 0, active: 0, idle: 0, activeWaiting: 0 };
  try {
    const { stdout } = await execFileAsync(
      "docker",
      [
        "exec", containerId, "psql", "-U", "postgres", "-d", "appsell", "-Atc",
        "select count(*),count(*) filter(where state='active')," +
          "count(*) filter(where state='idle')," +
          "count(*) filter(where state='active' and wait_event is not null) " +
          "from pg_stat_activity where datname=current_database()"
      ],
      { timeout: 3_000 }
    );
    const [total, active, idle, activeWaiting] = stdout.trim().split("|").map(Number);
    return {
      total: total || 0,
      active: active || 0,
      idle: idle || 0,
      activeWaiting: activeWaiting || 0
    };
  } catch {
    return { total: 0, active: 0, idle: 0, activeWaiting: 0 };
  }
}

async function buildReport(
  manifest: FixtureManifest,
  scenarios: Record<string, ScenarioResult>,
  resources: ResourcePeaks,
  containers: Record<string, string>,
  runtimeTeardown: Record<string, unknown>,
  warmupDurationMs: number,
  failure: unknown
) {
  let dockerVersion = "unavailable";
  try {
    dockerVersion = (await execFileAsync(
      "docker",
      ["version", "--format", "{{.Server.Version}}"],
      { timeout: 3_000 }
    )).stdout.trim();
  } catch {
    // Report remains useful when Docker version metadata cannot be read.
  }
  const imageIds = await containerImageIds(containers);
  const sourceEvidence = await worktreeEvidence();
  const runtimeMetadata = await toolAndDockerMetadata();
  const allScenarios = Object.values(scenarios);
  const unexpected5xx = allScenarios.reduce(
    (sum, scenario) => sum + Object.entries(scenario.statuses).reduce(
      (statusSum, [status, count]) => statusSum + (Number(status) >= 500 ? count : 0),
      0
    ),
    0
  );
  const crossTenant2xx = ["cross_tenant_denial", "cross_tenant_asset_denial"].reduce(
    (scenarioSum, name) => scenarioSum + Object.entries(scenarios[name]?.statuses ?? {}).reduce(
      (sum, [status, count]) =>
        sum + (Number(status) >= 200 && Number(status) < 300 ? count : 0),
      0
    ),
    0
  );
  const workerRss = Number(requiredEnvironment("APPSELL_CAPACITY_STORAGE_VERTICAL_TREE_RSS_BYTES"));
  return {
    schema_version: 1,
    status: failure ? "failed" : "passed",
    mode: "local_ci",
    production_certified: false,
    generated_at: new Date().toISOString(),
    git_sha: process.env.GITHUB_SHA || sourceEvidence.git_sha,
    source_evidence: sourceEvidence,
    tenant_count: manifest.tenant_count,
    distinct_subjects: manifest.distinct_subjects,
    owner_sessions: manifest.tenant_count,
    buyer_sessions: manifest.tenant_count,
    concurrency: CONCURRENCY,
    bff_instances: 1,
    warmup: { excluded_from_scenarios: true, duration_ms: warmupDurationMs },
    representative_s3_vertical: requiredEnvironment("APPSELL_CAPACITY_STORAGE_VERTICAL") === "passed",
    zero_error_budget: {
      unexpected_5xx: unexpected5xx,
      cross_tenant_successes: crossTenant2xx,
      request_failures: allScenarios.reduce(
        (sum, scenario) => sum + scenario.unexpected_errors,
        0
      ),
      timeouts: allScenarios.reduce((sum, scenario) => sum + scenario.timeouts, 0),
      payload_validation_failures: allScenarios.reduce(
        (sum, scenario) => sum + scenario.validation_failures,
        0
      )
    },
    scenarios,
    resources: { ...resources, storage_vertical_process_tree_rss_bytes: workerRss },
    hardware: {
      platform: platform(),
      release: release(),
      architecture: arch(),
      cpu_model: cpus()[0]?.model ?? "unknown",
      logical_cpus: cpus().length,
      total_memory_bytes: totalmem(),
      free_memory_bytes_at_report: freemem(),
      node: process.version,
      docker_server: dockerVersion,
      ...runtimeMetadata
    },
    runtime_teardown: runtimeTeardown,
    container_image_ids: imageIds,
    thresholds: {
      calibration: {
        status: "provisional_until_three_green_ci_runs",
        required_green_ci_runs: 3
      },
      oauth_callback_and_sync: {
        p95_ms: 1_200, p99_ms: 2_000, max_ms: 3_000, min_requests_per_second: 8
      },
      owner_catalog: {
        p95_ms: 750, p99_ms: 1_200, max_ms: 2_500, min_requests_per_second: 10
      },
      product_create: {
        p95_ms: 1_200, p99_ms: 2_000, max_ms: 3_000, min_requests_per_second: 8
      },
      product_create_verify: {
        p95_ms: 750, p99_ms: 1_200, max_ms: 2_500, min_requests_per_second: 10
      },
      buyer_library: {
        p95_ms: 750, p99_ms: 1_200, max_ms: 2_500, min_requests_per_second: 10
      },
      buyer_asset: {
        p95_ms: 1_200, p99_ms: 2_000, max_ms: 3_000, min_requests_per_second: 8
      },
      cross_tenant_denial: {
        p95_ms: 750, p99_ms: 1_200, max_ms: 2_500, min_requests_per_second: 10
      },
      cross_tenant_asset_denial: {
        p95_ms: 750, p99_ms: 1_200, max_ms: 2_500, min_requests_per_second: 10
      },
      max_postgres_connections: 12,
      resource_rss_bytes: {
        bff: 2 * 1024 ** 3,
        redis: 128 * 1024 ** 2,
        postgres: 512 * 1024 ** 2,
        api: 512 * 1024 ** 2,
        pdf_sandbox: 768 * 1024 ** 2,
        seaweedfs: 512 * 1024 ** 2,
        storage_vertical_process_tree: 1024 ** 3
      }
    },
    limits: {
      scanner: "deterministic_local_ci_not_clamav",
      asset_session_scope: "100_tenant_legacy_bundle_through_bff",
      s3_vertical_scope: "single_representative_tenant_direct_worker_and_storage",
      high_availability: false,
      staging_load_2x: false,
      vps_sizing: false
    },
    failure: failure ? safeError(failure) : null
  };
}

async function containerImageIds(containers: Record<string, string>) {
  return Object.fromEntries(await Promise.all(Object.entries(containers).map(async ([name, id]) => {
    try {
      const { stdout } = await execFileAsync(
        "docker",
        ["inspect", "--format", "{{.Image}}", id],
        { timeout: 3_000 }
      );
      return [name, stdout.trim()];
    } catch {
      return [name, "unavailable"];
    }
  })));
}

async function worktreeEvidence() {
  try {
    const [
      { stdout: gitSha },
      { stdout: status },
      { stdout: trackedDiff },
      { stdout: untracked }
    ] = await Promise.all([
      execFileAsync("git", ["rev-parse", "HEAD"], { timeout: 3_000 }),
      execFileAsync("git", ["status", "--porcelain=v1"], { timeout: 3_000 }),
      execFileAsync("git", ["diff", "--binary", "HEAD"], {
        timeout: 3_000,
        maxBuffer: 10 * 1024 * 1024
      }),
      execFileAsync(
        "git",
        ["ls-files", "--others", "--exclude-standard", "-z"],
        { timeout: 3_000 }
      )
    ]);
    const paths = untracked.split("\0").filter(Boolean).sort();
    const digest = createHash("sha256");
    digest.update(status);
    digest.update(trackedDiff);
    for (const path of paths) {
      digest.update(path);
      digest.update("\0");
      try {
        digest.update(await readFile(join(process.cwd(), path)));
      } catch {
        digest.update("<deleted>");
      }
      digest.update("\0");
    }
    return {
      git_sha: gitSha.trim(),
      dirty: status.trim().length > 0,
      changed_file_count: status.trim() ? status.trim().split("\n").length : 0,
      worktree_evidence_sha256: digest.digest("hex")
    };
  } catch {
    return {
      git_sha: "unavailable",
      dirty: true,
      changed_file_count: -1,
      worktree_evidence_sha256: "unavailable"
    };
  }
}

async function toolAndDockerMetadata() {
  const command = async (file: string, args: string[]) => {
    try {
      return (await execFileAsync(file, args, { timeout: 3_000 })).stdout.trim();
    } catch {
      return "unavailable";
    }
  };
  const [python, uv, pnpm, dockerMemory, dockerCpus] = await Promise.all([
    command("uv", ["run", "--project", "apps/api", "python", "--version"]),
    command("uv", ["--version"]),
    command("pnpm", ["--version"]),
    command("docker", ["info", "--format", "{{.MemTotal}}"]),
    command("docker", ["info", "--format", "{{.NCPU}}"])
  ]);
  return {
    python,
    uv,
    pnpm,
    docker_memory_limit_bytes: Number(dockerMemory) || null,
    docker_logical_cpus: Number(dockerCpus) || null
  };
}

async function writeCapacityReport(report: Record<string, unknown>) {
  const output = requiredEnvironment("APPSELL_CAPACITY_REPORT");
  await writeFile(output, `${JSON.stringify(report, null, 2)}\n`, { encoding: "utf8" });
  const scenarioRows = Object.entries(report.scenarios as Record<string, ScenarioResult>).map(
    ([name, scenario]) =>
      `| ${name} | ${scenario.requests} | ${scenario.latency_ms.p50} | ${scenario.latency_ms.p95} | ${scenario.latency_ms.p99} | ${scenario.latency_ms.max} | ${scenario.throughput_per_second} | ${scenario.unexpected_errors} |`
  );
  const resources = report.resources as ResourcePeaks & {
    storage_vertical_process_tree_rss_bytes: number;
  };
  const markdown = [
    "# AppSell capacity baseline — 100 tenants",
    "",
    `- Status: **${String(report.status)}**`,
    `- Mode: Local/CI`,
    `- Tenants: ${String(report.tenant_count)}`,
    `- Distinct OAuth subjects: ${String(report.distinct_subjects)}`,
    `- Concurrency: ${String(report.concurrency)}`,
    `- Production certified: **no**`,
    "",
    "| Scenario | Requests | p50 ms | p95 ms | p99 ms | max ms | req/s | errors |",
    "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ...scenarioRows,
    "",
    "## Peak resources",
    "",
    `- BFF RSS: ${resources.bff_rss_bytes} bytes`,
    `- Storage vertical process-tree RSS: ${resources.storage_vertical_process_tree_rss_bytes} bytes`,
    `- Redis RSS: ${resources.redis_rss_bytes} bytes`,
    `- Postgres connections: ${resources.postgres_connections}`,
    "",
    "This artifact is a regression baseline for disposable Local/CI infrastructure. It is not VPS, staging, HA, restore, ClamAV, billing, DNS or production evidence.",
    ""
  ].join("\n");
  await writeFile(output.replace(/\.json$/, ".md"), markdown, { encoding: "utf8" });
}

function assertFixture(manifest: FixtureManifest) {
  if (
    manifest.schema_version !== 1 || manifest.mode !== "local_ci" ||
    manifest.tenant_count !== CAPACITY || manifest.distinct_subjects !== CAPACITY * 2 ||
    manifest.tenants.length !== CAPACITY
  ) {
    throw new Error("invalid capacity fixture manifest");
  }
  const sellers = new Set(manifest.tenants.map((tenant) => tenant.seller_id));
  const products = new Set(manifest.tenants.map((tenant) => tenant.product_id));
  const subjects = new Set(manifest.tenants.flatMap((tenant) => [tenant.owner_subject, tenant.buyer_subject]));
  if (sellers.size !== CAPACITY || products.size !== CAPACITY || subjects.size !== CAPACITY * 2) {
    throw new Error("capacity fixture does not contain distinct tenants and subjects");
  }
}

function requiredSession<T>(sessions: Map<number, T>, ordinal: number, role: string) {
  const value = sessions.get(ordinal);
  if (!value) throw new Error(`missing ${role} session for tenant ${ordinal}`);
  return value;
}

function percentile(values: number[], percentileValue: number) {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  const index = Math.max(0, Math.ceil((percentileValue / 100) * sorted.length) - 1);
  return round(sorted[index] ?? 0);
}

function round(value: number) {
  return Math.round(value * 100) / 100;
}

function origin(port: number) {
  return `http://127.0.0.1:${port}`;
}

function timedFetch(input: string | URL, init: RequestInit = {}, timeoutMs = 5_000) {
  return fetch(input, { ...init, signal: AbortSignal.timeout(timeoutMs) });
}

async function requireStatus(responsePromise: Promise<Response>, expected: number, label: string) {
  const response = await responsePromise;
  if (response.status !== expected) {
    throw new Error(`${label}: expected ${expected}, got ${response.status}: ${await response.text()}`);
  }
}

function cookiePair(setCookie: string | null, name: string) {
  const match = setCookie?.match(new RegExp(`(?:^|,\\s*)(${name}=[^;]+)`));
  if (!match) throw new Error(`Missing ${name} cookie.`);
  return match[1] ?? "";
}

function requiredEnvironment(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`Missing capacity test setting: ${name}`);
  return value;
}

function safeError(error: unknown) {
  return error instanceof Error ? `${error.name}: ${error.message}`.slice(0, 1_000) : "unknown error";
}

// Kept below the test helpers so the spawned process cannot accidentally inherit
// fixture paths or database superuser credentials not explicitly provided.
function startBff(port: number, distDir: string, shared: Record<string, string>) {
  return spawn(
    process.execPath,
    [join(process.cwd(), "node_modules/next/dist/bin/next"), "dev", "-H", "127.0.0.1", "-p", String(port)],
    {
      cwd: process.cwd(),
      env: {
        PATH: process.env.PATH,
        HOME: process.env.HOME,
        NODE_ENV: "development",
        ...shared,
        APPSELL_NEXT_DIST_DIR: distDir
      },
      stdio: ["ignore", "pipe", "pipe"]
    }
  );
}

async function waitForBff(process: ChildProcess, port: number) {
  const deadline = Date.now() + 30_000;
  let diagnostics = "";
  process.stdout?.on("data", (chunk) => { diagnostics = `${diagnostics}${String(chunk)}`.slice(-4_000); });
  process.stderr?.on("data", (chunk) => { diagnostics = `${diagnostics}${String(chunk)}`.slice(-4_000); });
  while (Date.now() < deadline) {
    if (process.exitCode !== null) throw new Error(`BFF ${port} exited: ${diagnostics}`);
    try {
      if ((await timedFetch(`${origin(port)}/`, {}, 1_000)).ok) return;
    } catch {
      // The isolated BFF is still starting.
    }
    await delay(100);
  }
  throw new Error(`BFF ${port} did not start: ${diagnostics}`);
}

async function stopBff(process: ChildProcess) {
  if (process.exitCode !== null) return;
  const exited = new Promise<void>((resolve) => process.once("exit", () => resolve()));
  process.kill("SIGTERM");
  await Promise.race([exited, delay(2_000)]);
  if (process.exitCode === null) {
    process.kill("SIGKILL");
    await exited;
  }
}

function delay(milliseconds: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, milliseconds));
}
