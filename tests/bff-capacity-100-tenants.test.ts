import { execFile, spawn, type ChildProcess } from "node:child_process";
import { createHash, randomBytes, randomUUID } from "node:crypto";
import { access, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { cpus, freemem, loadavg, platform, arch, release, tmpdir, totalmem } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";

import { expect, test } from "vitest";

import { RedisOAuthService } from "../lib/redis-oauth-service";
import {
  calculatePostgresHeadroom,
  postgresHeadroomFailures
} from "../lib/capacity-postgres-headroom";
import {
  accumulatePostgresWaitDiagnostics,
  emptyPostgresWaitDiagnostics,
  samplePostgresConnections
} from "../lib/capacity-postgres-sampler";
import {
  aggregateCapacityProductCreateTraces,
  capacityProductCreateTelemetryEnabled,
  parseCapacityProductCreateTraceLine,
  validCapacityProductCreateTrace,
  type CapacityProductCreateTelemetryAggregate,
  type CapacityProductCreateTrace
} from "../lib/capacity-product-create-telemetry";
import { startIsolatedRedis, unusedNonDefaultPort } from "./helpers/isolated-redis";

const execFileAsync = promisify(execFile);
const domainApiUrl = process.env.APPSELL_TEST_DOMAIN_API_URL;
const TENANT_CAPACITY = 100;
const BUYERS_PER_TENANT = 10;
const BUYER_CAPACITY = TENANT_CAPACITY * BUYERS_PER_TENANT;
const CONCURRENCY = 10;
const CAPACITY_TEST_TIMEOUT_MS = 900_000;
const CAPACITY_TRACE_DRAIN_TIMEOUT_MS = 30_000;
const OAUTH_RATE_WINDOW_MS = 60_000;
const OAUTH_FIRST_WINDOW_CAPACITY = 990;
const DIAGNOSTIC_CLOCK_ORIGIN_MS = performance.now();
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

interface ScenarioThreshold {
  p95: number;
  p99: number;
  max: number;
  throughput: number;
}

const SCENARIO_THRESHOLDS = {
  oauth_callback_and_sync: { p95: 1_750, p99: 2_250, max: 3_000, throughput: 10 },
  owner_catalog: { p95: 2_250, p99: 2_500, max: 3_000, throughput: 10 },
  product_create: { p95: 2_250, p99: 2_750, max: 3_000, throughput: 8 },
  product_create_verify: { p95: 2_250, p99: 2_500, max: 3_000, throughput: 10 },
  buyer_library: { p95: 2_250, p99: 2_500, max: 3_000, throughput: 10 },
  buyer_product_denial: { p95: 2_250, p99: 2_500, max: 3_000, throughput: 10 },
  buyer_player: { p95: 2_250, p99: 2_500, max: 3_000, throughput: 10 },
  cross_tenant_player_denial: { p95: 2_250, p99: 2_500, max: 3_000, throughput: 10 },
  buyer_progress: { p95: 2_250, p99: 2_750, max: 3_000, throughput: 8 },
  cross_tenant_progress_denial: { p95: 2_250, p99: 2_500, max: 3_000, throughput: 10 },
  buyer_asset: { p95: 2_250, p99: 2_750, max: 3_000, throughput: 8 },
  cross_tenant_denial: { p95: 2_250, p99: 2_500, max: 3_000, throughput: 10 },
  cross_tenant_asset_denial: { p95: 2_250, p99: 2_500, max: 3_000, throughput: 10 }
} satisfies Record<string, ScenarioThreshold>;

interface FixtureBuyer {
  ordinal: number;
  subject: string;
  identity_id: string;
  entitlement_id: string;
}

interface FixtureTenant {
  ordinal: number;
  owner_subject: string;
  seller_id: string;
  seller_slug: string;
  product_id: string;
  product_slug: string;
  product_title: string;
  buyers: FixtureBuyer[];
  publication_id: string;
  lesson_id: string;
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
  admin_count: number;
  buyers_per_tenant: number;
  buyer_count: number;
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
  excluded_wait_ms: number;
  throughput_per_second: number;
  latency_ms: { p50: number; p95: number; p99: number; max: number };
  wave_diagnostics: ScenarioWaveDiagnostic[];
}

interface RequestObservation {
  index: number;
  started_ms: number;
  ended_ms: number;
  status: number | null;
  expected: number | null;
  valid: boolean | null;
  timeout: boolean;
}

interface ScenarioWaveDiagnostic {
  ordinal: number;
  start_ms: number;
  end_ms: number;
  elapsed_ms: number;
  requests: number;
  responses: number;
  exceptions: number;
  statuses: Record<string, number>;
  expected_matches: number;
  validation_failures: number;
  timeouts: number;
  latency_ms: { p95: number; max: number };
}

interface BffHostResourceSample {
  elapsed_ms: number;
  phase: string;
  process_tree_sample_valid: boolean;
  bff_cpu_percent: number | null;
  host_cpu_percent: number;
  load_average: { one_minute: number; five_minutes: number; fifteen_minutes: number };
}

interface ProcessTreeStats {
  rssBytes: number;
  cpuPercent: number;
}

interface ProcessTreeSample {
  valid: boolean;
  bff: ProcessTreeStats;
  redis: ProcessTreeStats;
}

interface ResourcePeaks {
  bff_rss_bytes: number;
  redis_rss_bytes: number;
  container_rss_bytes: Record<string, number>;
  postgres_connections: number;
  postgres_workload_connections: number;
  postgres_runtime_connections: number;
  postgres_api_connections: number;
  postgres_active_connections: number;
  postgres_idle_connections: number;
  postgres_active_waiting: number;
  postgres_lock_waiting: number;
  postgres_lwlock_waiting: number;
  postgres_buffer_pin_waiting: number;
  postgres_client_waiting: number;
  postgres_io_waiting: number;
  postgres_ipc_waiting: number;
  postgres_activity_waiting: number;
  postgres_timeout_waiting: number;
  postgres_extension_waiting: number;
  postgres_other_waiting: number;
  postgres_samples_with_active_waiting: number;
  postgres_samples_with_lock_waiting: number;
  postgres_active_waiting_observed_ms: number;
  postgres_wait_event_peaks: Record<string, number>;
  postgres_api_active_waiting: number;
  postgres_api_wait_event_peaks: Record<string, number>;
  postgres_waiting_samples_by_phase: Record<string, number>;
  postgres_full_api_pool_waiting_samples: number;
  postgres_full_api_pool_waiting_observed_ms: number;
  postgres_max_consecutive_full_api_pool_waiting_samples: number;
  postgres_max_consecutive_full_api_pool_waiting_ms: number;
  postgres_sample_errors: number;
  postgres_max_sample_gap_ms: number;
  postgres_max_connections: number;
  postgres_reserved_connections: number;
  bff_cpu_percent: number;
  host_cpu_percent: number;
  process_tree_sample_errors: number;
  bff_host_samples: BffHostResourceSample[];
  samples: number;
}

test.skipIf(!domainApiUrl)(
  "certifies 100 admins and 1,000 buyers across 100 isolated tenants in Local/CI",
  async () => {
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
  let bffTraceCollector: ReturnType<typeof startBffCapacityTraceCollector> | undefined;
  let oauth: RedisOAuthService | undefined;
  let sampler: ReturnType<typeof startResourceSampler> | undefined;
  let port = 0;
  const suffix = `${process.pid}-${Date.now()}`;
  const distDir = `.next-capacity-${suffix}`;
  const scenarios: Record<string, ScenarioResult> = {};
  const thresholdFailures: string[] = [];
  let testFailure: unknown;
  let peaks = emptyResourcePeaks(containers);
  const cleanupErrors: string[] = [];
  let redisKeysBeforeShutdown = 0;
  let warmupDurationMs = 0;
  let observedOwnerSessions = 0;
  let observedBuyerSessions = 0;
  let failurePhase = "runtime_startup";
  let productCreateTelemetry = aggregateCapacityProductCreateTraces([], [], 0, true);

  try {
    redis = await startIsolatedRedis();
    port = await unusedNonDefaultPort();
    const shared = capacityEnvironment(redis.url, port, suffix);
    bff = startBff(port, distDir, shared);
    bffTraceCollector = startBffCapacityTraceCollector(bff);
    const activeOauth = RedisOAuthService.fromEnvironment(shared);
    oauth = activeOauth;
    await waitForBff(bff, port);
    sampler = startResourceSampler(bff.pid!, redis.pid, containers, () => failurePhase);

    // Warm compilation, OAuth callback, domain sync and both read route families.
    const warmupStarted = performance.now();
    failurePhase = "warmup_owner_session";
    const warmOwner = await createSession(
      activeOauth,
      port,
      manifest.tenants[0]!.owner_subject,
      "warm-owner",
      30_000
    );
    failurePhase = "warmup_buyer_session";
    const warmBuyer = await createSession(
      activeOauth,
      port,
      manifest.tenants[0]!.buyers[0]!.subject,
      "warm-buyer",
      30_000
    );
    failurePhase = "warmup_owner_catalog";
    await requireStatus(
      timedFetch(`${origin(port)}/api/sellers/${manifest.tenants[0]!.seller_id}/products`, {
        headers: { cookie: warmOwner.sessionCookie }
      }, 30_000),
      200,
      "warm owner catalog"
    );
    failurePhase = "warmup_buyer_library";
    await requireStatus(
      timedFetch(
        `${origin(port)}/api/library`,
        { headers: { cookie: warmBuyer.sessionCookie } },
        30_000
      ),
      200,
      "warm buyer library"
    );
    failurePhase = "warmup_cross_tenant_product";
    await requireStatus(
      timedFetch(
        `${origin(port)}/api/sellers/${manifest.tenants[1]!.seller_id}/products/${manifest.tenants[1]!.product_id}`,
        {
          headers: { cookie: warmOwner.sessionCookie }
        },
        30_000
      ),
      404,
      "warm cross-tenant denial"
    );
    failurePhase = "warmup_product_create";
    const warmProduct = await timedFetch(
      `${origin(port)}/api/sellers/${manifest.tenants[0]!.seller_id}/products`,
      {
        method: "POST",
        headers: {
          cookie: warmOwner.cookieHeader,
          origin: shared.APPSELL_PUBLIC_ORIGIN
        },
        body: new URLSearchParams({
          _csrf: warmOwner.productCsrf,
          idempotency_key: randomUUID(),
          slug: "capacity-warm-product",
          product_title: "Produto de warmup",
          product_description: "Fora das métricas.",
          course_title: "Curso de warmup"
        })
      },
      30_000
    );
    if (warmProduct.status !== 201) {
      throw new Error(`warm product create expected 201, got ${warmProduct.status}`);
    }
    const warmProductPayload = await warmProduct.json() as { product_id?: string };
    if (typeof warmProductPayload.product_id !== "string") {
      throw new Error("warm product create did not return a product id");
    }
    const warmProductId = warmProductPayload.product_id;
    failurePhase = "warmup_product_verify";
    await requireStatus(
      timedFetch(
        `${origin(port)}/api/sellers/${manifest.tenants[0]!.seller_id}/products/${warmProductId}`,
        { headers: { cookie: warmOwner.sessionCookie } },
        30_000
      ),
      200,
      "warm product verify"
    );
    failurePhase = "warmup_buyer_player";
    const warmPlayer = await timedFetch(
      `${origin(port)}/api/library/items/${manifest.tenants[0]!.buyers[0]!.entitlement_id}` +
        `/lessons/${manifest.tenants[0]!.lesson_id}`,
      { headers: { cookie: warmBuyer.sessionCookie } },
      30_000
    );
    if (warmPlayer.status !== 200) {
      throw new Error(`warm buyer player expected 200, got ${warmPlayer.status}`);
    }
    const warmPlayerPayload = await warmPlayer.json() as { csrf?: { progress?: string } };
    if (typeof warmPlayerPayload.csrf?.progress !== "string") {
      throw new Error("warm buyer player did not return progress CSRF");
    }
    failurePhase = "warmup_cross_tenant_progress";
    await requireStatus(
      timedFetch(
        `${origin(port)}/api/library/items/${manifest.tenants[1]!.buyers[0]!.entitlement_id}` +
          `/lessons/${manifest.tenants[1]!.lesson_id}/progress`,
        {
          method: "PUT",
          headers: {
            cookie: warmBuyer.sessionCookie,
            origin: shared.APPSELL_PUBLIC_ORIGIN
          },
          body: new URLSearchParams({
            _csrf: warmPlayerPayload.csrf.progress,
            idempotency_key: randomUUID(),
            progress_percent: "50",
            expected_version: "0"
          })
        },
        30_000
      ),
      404,
      "warm cross-tenant progress denial"
    );
    failurePhase = "warmup_own_asset";
    const warmAsset = await timedFetch(
      `${origin(port)}/api/library/items/${manifest.tenants[0]!.buyers[0]!.entitlement_id}/assets/${manifest.tenants[0]!.asset_id}`,
      { headers: { cookie: warmBuyer.sessionCookie } },
      30_000
    );
    if (warmAsset.status !== 200 || (await warmAsset.arrayBuffer()).byteLength === 0) {
      throw new Error(`warm own asset expected non-empty 200, got ${warmAsset.status}`);
    }
    failurePhase = "warmup_cross_tenant_asset";
    await requireStatus(
      timedFetch(
        `${origin(port)}/api/library/items/${manifest.tenants[1]!.buyers[0]!.entitlement_id}/assets/${manifest.tenants[1]!.asset_id}`,
        { headers: { cookie: warmBuyer.sessionCookie } },
        30_000
      ),
      404,
      "warm cross-tenant asset denial"
    );
    warmupDurationMs = round(performance.now() - warmupStarted);
    failurePhase = "measured_workload";

    const ownerSessions = new Map<number, CapacitySession>();
    const buyerSessions = new Map<string, CapacitySession>();
    failurePhase = "scenario_oauth_callback_and_sync";
    scenarios.oauth_callback_and_sync = await measureScenario(
      manifest.tenants.flatMap((tenant) => [
        { tenant, role: "owner" as const, subject: tenant.owner_subject, buyer: undefined },
        ...tenant.buyers.map((buyer) => ({
          tenant,
          role: "buyer" as const,
          subject: buyer.subject,
          buyer
        }))
      ]),
      async ({ tenant, role, subject, buyer }) => {
        const cookie = await createSession(
          activeOauth,
          port,
          subject,
          role === "owner"
            ? `capacity-owner-${tenant.ordinal.toString().padStart(3, "0")}`
            : `capacity-buyer-${tenant.ordinal.toString().padStart(3, "0")}-${buyer!.ordinal.toString().padStart(2, "0")}`
        );
        if (role === "owner") {
          ownerSessions.set(tenant.ordinal, cookie);
        } else {
          buyerSessions.set(buyerSessionKey(tenant.ordinal, buyer!.ordinal), cookie);
        }
        return { status: 307, expected: 307 };
      },
      {
        pauseAfter: OAUTH_FIRST_WINDOW_CAPACITY,
        betweenBatches: waitForNextOauthRateWindow
      }
    );
    recordThreshold(thresholdFailures,
      "oauth_callback_and_sync",
      scenarios.oauth_callback_and_sync,
      SCENARIO_THRESHOLDS.oauth_callback_and_sync
    );
    requireScenarioClean("oauth_callback_and_sync", scenarios.oauth_callback_and_sync);
    observedOwnerSessions = ownerSessions.size;
    observedBuyerSessions = buyerSessions.size;
    if (
      observedOwnerSessions !== TENANT_CAPACITY ||
      observedBuyerSessions !== BUYER_CAPACITY
    ) {
      throw new Error("oauth session cardinality mismatch");
    }

    failurePhase = "scenario_owner_catalog";
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
    recordThreshold(
      thresholdFailures,
      "owner_catalog",
      scenarios.owner_catalog,
      SCENARIO_THRESHOLDS.owner_catalog
    );
    requireScenarioClean("owner_catalog", scenarios.owner_catalog);

    const createdProducts = new Map<number, { productId: string; title: string }>();
    failurePhase = "scenario_product_create";
    scenarios.product_create = await measureScenario(manifest.tenants, async (tenant) => {
      const session = requiredSession(ownerSessions, tenant.ordinal, "owner");
      const response = await timedFetch(
        `${origin(port)}/api/sellers/${tenant.seller_id}/products`,
        {
          method: "POST",
          headers: {
            cookie: session.cookieHeader,
            origin: shared.APPSELL_PUBLIC_ORIGIN,
            "x-appsell-capacity-trace": `pc-${tenant.ordinal.toString().padStart(3, "0")}`
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
    recordThreshold(
      thresholdFailures,
      "product_create",
      scenarios.product_create,
      SCENARIO_THRESHOLDS.product_create
    );
    requireScenarioClean("product_create", scenarios.product_create);

    failurePhase = "scenario_product_create_verify";
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
    recordThreshold(thresholdFailures,
      "product_create_verify",
      scenarios.product_create_verify,
      SCENARIO_THRESHOLDS.product_create_verify
    );
    requireScenarioClean("product_create_verify", scenarios.product_create_verify);

    const tenantBuyers = manifest.tenants.flatMap((tenant) =>
      tenant.buyers.map((buyer) => ({ tenant, buyer }))
    );
    failurePhase = "scenario_buyer_library";
    scenarios.buyer_library = await measureScenario(tenantBuyers, async ({ tenant, buyer }) => {
      const response = await timedFetch(`${origin(port)}/api/library`, {
        headers: {
          cookie: requiredSession(
            buyerSessions,
            buyerSessionKey(tenant.ordinal, buyer.ordinal),
            "buyer"
          ).sessionCookie
        }
      });
      const payload = await response.clone().json() as {
        items?: Array<{ entitlement_id?: string; product?: { id?: string; title?: string } }>;
      };
      const serialized = JSON.stringify(payload);
      const next = manifest.tenants[(tenant.ordinal + 1) % TENANT_CAPACITY]!;
      const own = payload.items?.length === 1 &&
        payload.items[0]?.entitlement_id === buyer.entitlement_id &&
        payload.items[0]?.product?.id === tenant.product_id &&
        payload.items[0]?.product?.title === tenant.product_title &&
        !serialized.includes(next.product_id) && !serialized.includes(next.product_title);
      return { status: response.status, expected: 200, valid: own };
    });
    recordThreshold(
      thresholdFailures,
      "buyer_library",
      scenarios.buyer_library,
      SCENARIO_THRESHOLDS.buyer_library
    );
    requireScenarioClean("buyer_library", scenarios.buyer_library);

    failurePhase = "scenario_buyer_product_denial";
    scenarios.buyer_product_denial = await measureScenario(
      tenantBuyers,
      async ({ tenant, buyer }) => {
        const target = manifest.tenants[(tenant.ordinal + 1) % TENANT_CAPACITY]!;
        const response = await timedFetch(
          `${origin(port)}/api/sellers/${target.seller_id}/products/${target.product_id}`,
          {
            headers: {
              cookie: requiredSession(
                buyerSessions,
                buyerSessionKey(tenant.ordinal, buyer.ordinal),
                "buyer"
              ).sessionCookie
            }
          }
        );
        return {
          status: response.status,
          expected: 404,
          valid: await genericDenialResponse(response, "product_not_found", [
            target.seller_id,
            target.product_id,
            target.product_title,
            target.publication_id
          ])
        };
      }
    );
    recordThreshold(
      thresholdFailures,
      "buyer_product_denial",
      scenarios.buyer_product_denial,
      SCENARIO_THRESHOLDS.buyer_product_denial
    );
    requireScenarioClean("buyer_product_denial", scenarios.buyer_product_denial);
    if (scenarios.buyer_product_denial.expected_denials !== BUYER_CAPACITY) {
      throw new Error("cross-tenant buyer product denial budget was not 1,000/1,000");
    }

    const playerStates = new Map<string, {
      csrf: string;
      enrollmentId: string;
      version: number;
    }>();
    failurePhase = "scenario_buyer_player";
    scenarios.buyer_player = await measureScenario(tenantBuyers, async ({ tenant, buyer }) => {
      const response = await timedFetch(
        `${origin(port)}/api/library/items/${buyer.entitlement_id}/lessons/${tenant.lesson_id}`,
        {
          headers: {
            cookie: requiredSession(
              buyerSessions,
              buyerSessionKey(tenant.ordinal, buyer.ordinal),
              "buyer"
            ).sessionCookie
          }
        }
      );
      const payload = await response.clone().json() as {
        player?: {
          seller?: { id?: string };
          product?: { id?: string };
          publication?: { id?: string };
          entitlement_id?: string;
          enrollment_id?: string;
          selected_lesson_id?: string;
          state_version?: number;
        };
        csrf?: { progress?: string };
      };
      const valid = payload.player?.seller?.id === tenant.seller_id &&
        payload.player?.product?.id === tenant.product_id &&
        payload.player?.publication?.id === tenant.publication_id &&
        payload.player?.entitlement_id === buyer.entitlement_id &&
        typeof payload.player?.enrollment_id === "string" &&
        UUID.test(payload.player.enrollment_id) &&
        payload.player?.selected_lesson_id === tenant.lesson_id &&
        payload.player?.state_version === 0 &&
        typeof payload.csrf?.progress === "string" && payload.csrf.progress.length >= 32;
      if (valid) {
        playerStates.set(buyerSessionKey(tenant.ordinal, buyer.ordinal), {
          csrf: payload.csrf!.progress!,
          enrollmentId: payload.player!.enrollment_id!,
          version: payload.player!.state_version!
        });
      }
      return { status: response.status, expected: 200, valid };
    });
    recordThreshold(
      thresholdFailures,
      "buyer_player",
      scenarios.buyer_player,
      SCENARIO_THRESHOLDS.buyer_player
    );
    requireScenarioClean("buyer_player", scenarios.buyer_player);
    if (
      playerStates.size !== BUYER_CAPACITY ||
      new Set([...playerStates.values()].map((state) => state.enrollmentId)).size !==
        BUYER_CAPACITY
    ) {
      throw new Error("buyer enrollment cardinality mismatch");
    }

    failurePhase = "scenario_cross_tenant_player_denial";
    scenarios.cross_tenant_player_denial = await measureScenario(
      tenantBuyers,
      async ({ tenant, buyer }) => {
        const target = manifest.tenants[(tenant.ordinal + 1) % TENANT_CAPACITY]!;
        const targetBuyer = target.buyers[buyer.ordinal]!;
        const response = await timedFetch(
          `${origin(port)}/api/library/items/${targetBuyer.entitlement_id}` +
            `/lessons/${target.lesson_id}`,
          {
            headers: {
              cookie: requiredSession(
                buyerSessions,
                buyerSessionKey(tenant.ordinal, buyer.ordinal),
                "buyer"
              ).sessionCookie
            }
          }
        );
        return {
          status: response.status,
          expected: 404,
          valid: await genericDenialResponse(response, "learning_item_unavailable", [
            target.seller_id,
            target.product_id,
            target.publication_id,
            targetBuyer.entitlement_id,
            target.lesson_id
          ])
        };
      }
    );
    recordThreshold(
      thresholdFailures,
      "cross_tenant_player_denial",
      scenarios.cross_tenant_player_denial,
      SCENARIO_THRESHOLDS.cross_tenant_player_denial
    );
    requireScenarioClean(
      "cross_tenant_player_denial",
      scenarios.cross_tenant_player_denial
    );
    if (scenarios.cross_tenant_player_denial.expected_denials !== BUYER_CAPACITY) {
      throw new Error("cross-tenant player denial budget was not 1,000/1,000");
    }

    failurePhase = "scenario_buyer_progress";
    scenarios.buyer_progress = await measureScenario(tenantBuyers, async ({ tenant, buyer }) => {
      const key = buyerSessionKey(tenant.ordinal, buyer.ordinal);
      const state = playerStates.get(key);
      if (!state) throw new Error(`missing player state for buyer ${key}`);
      const response = await timedFetch(
        `${origin(port)}/api/library/items/${buyer.entitlement_id}/lessons/${tenant.lesson_id}/progress`,
        {
          method: "PUT",
          headers: {
            cookie: requiredSession(buyerSessions, key, "buyer").sessionCookie,
            origin: shared.APPSELL_PUBLIC_ORIGIN
          },
          body: new URLSearchParams({
            _csrf: state.csrf,
            idempotency_key: randomUUID(),
            progress_percent: "25",
            expected_version: String(state.version)
          })
        }
      );
      const payload = await response.clone().json() as {
        progress?: {
          entitlement_id?: string;
          enrollment_id?: string;
          lesson_id?: string;
          lesson_progress_percent?: number;
          state_version?: number;
          applied?: boolean;
        };
      };
      const valid = payload.progress?.entitlement_id === buyer.entitlement_id &&
        payload.progress?.enrollment_id === state.enrollmentId &&
        payload.progress?.lesson_id === tenant.lesson_id &&
        payload.progress?.lesson_progress_percent === 25 &&
        payload.progress?.state_version === 1 && payload.progress?.applied === true;
      return { status: response.status, expected: 200, valid };
    });
    recordThreshold(
      thresholdFailures,
      "buyer_progress",
      scenarios.buyer_progress,
      SCENARIO_THRESHOLDS.buyer_progress
    );
    requireScenarioClean("buyer_progress", scenarios.buyer_progress);

    failurePhase = "scenario_cross_tenant_progress_denial";
    scenarios.cross_tenant_progress_denial = await measureScenario(
      tenantBuyers,
      async ({ tenant, buyer }) => {
        const key = buyerSessionKey(tenant.ordinal, buyer.ordinal);
        const state = playerStates.get(key);
        if (!state) throw new Error(`missing player state for buyer ${key}`);
        const target = manifest.tenants[(tenant.ordinal + 1) % TENANT_CAPACITY]!;
        const targetBuyer = target.buyers[buyer.ordinal]!;
        const response = await timedFetch(
          `${origin(port)}/api/library/items/${targetBuyer.entitlement_id}` +
            `/lessons/${target.lesson_id}/progress`,
          {
            method: "PUT",
            headers: {
              cookie: requiredSession(buyerSessions, key, "buyer").sessionCookie,
              origin: shared.APPSELL_PUBLIC_ORIGIN
            },
            body: new URLSearchParams({
              _csrf: state.csrf,
              idempotency_key: randomUUID(),
              progress_percent: "50",
              expected_version: "1"
            })
          }
        );
        return {
          status: response.status,
          expected: 404,
          valid: await genericDenialResponse(response, "learning_item_unavailable", [
            target.seller_id,
            target.product_id,
            target.publication_id,
            targetBuyer.entitlement_id,
            target.lesson_id
          ])
        };
      }
    );
    recordThreshold(
      thresholdFailures,
      "cross_tenant_progress_denial",
      scenarios.cross_tenant_progress_denial,
      SCENARIO_THRESHOLDS.cross_tenant_progress_denial
    );
    requireScenarioClean(
      "cross_tenant_progress_denial",
      scenarios.cross_tenant_progress_denial
    );
    if (scenarios.cross_tenant_progress_denial.expected_denials !== BUYER_CAPACITY) {
      throw new Error("cross-tenant progress denial budget was not 1,000/1,000");
    }

    failurePhase = "scenario_buyer_asset";
    scenarios.buyer_asset = await measureScenario(tenantBuyers, async ({ tenant, buyer }) => {
      const response = await timedFetch(
        `${origin(port)}/api/library/items/${buyer.entitlement_id}/assets/${tenant.asset_id}`,
        {
          headers: {
            cookie: requiredSession(
              buyerSessions,
              buyerSessionKey(tenant.ordinal, buyer.ordinal),
              "buyer"
            ).sessionCookie
          }
        }
      );
      const payload = response.status === 200 ? await response.arrayBuffer() : new ArrayBuffer(0);
      const valid = response.headers.get("content-type") === "application/pdf" &&
        payload.byteLength > 0 &&
        createHash("sha256").update(new Uint8Array(payload)).digest("hex") === tenant.asset_sha256;
      return { status: response.status, expected: 200, valid };
    });
    recordThreshold(
      thresholdFailures,
      "buyer_asset",
      scenarios.buyer_asset,
      SCENARIO_THRESHOLDS.buyer_asset
    );
    requireScenarioClean("buyer_asset", scenarios.buyer_asset);

    failurePhase = "scenario_cross_tenant_denial";
    scenarios.cross_tenant_denial = await measureScenario(manifest.tenants, async (tenant) => {
      const target = manifest.tenants[(tenant.ordinal + 1) % TENANT_CAPACITY]!;
      const response = await timedFetch(
        `${origin(port)}/api/sellers/${target.seller_id}/products/${target.product_id}`,
        {
          headers: {
            cookie: requiredSession(ownerSessions, tenant.ordinal, "owner").sessionCookie
          }
        }
      );
      return {
        status: response.status,
        expected: 404,
        valid: await genericDenialResponse(response, "product_not_found", [
          target.seller_id,
          target.product_id,
          target.product_title,
          target.publication_id
        ])
      };
    });
    recordThreshold(thresholdFailures,
      "cross_tenant_denial",
      scenarios.cross_tenant_denial,
      SCENARIO_THRESHOLDS.cross_tenant_denial
    );
    requireScenarioClean("cross_tenant_denial", scenarios.cross_tenant_denial);
    if (scenarios.cross_tenant_denial.expected_denials !== TENANT_CAPACITY) {
      throw new Error("cross-tenant owner denial budget was not 100/100");
    }

    failurePhase = "scenario_cross_tenant_asset_denial";
    scenarios.cross_tenant_asset_denial = await measureScenario(
      tenantBuyers,
      async ({ tenant, buyer }) => {
        const target = manifest.tenants[(tenant.ordinal + 1) % TENANT_CAPACITY]!;
        const targetBuyer = target.buyers[buyer.ordinal]!;
        const response = await timedFetch(
          `${origin(port)}/api/library/items/${targetBuyer.entitlement_id}/assets/${target.asset_id}`,
          {
            headers: {
              cookie: requiredSession(
                buyerSessions,
                buyerSessionKey(tenant.ordinal, buyer.ordinal),
                "buyer"
              ).sessionCookie
            }
          }
        );
        return {
          status: response.status,
          expected: 404,
          valid: await genericDenialResponse(response, "learning_item_unavailable", [
            target.seller_id,
            target.product_id,
            target.publication_id,
            targetBuyer.entitlement_id,
            target.lesson_id,
            target.asset_id
          ])
        };
      }
    );
    recordThreshold(thresholdFailures,
      "cross_tenant_asset_denial",
      scenarios.cross_tenant_asset_denial,
      SCENARIO_THRESHOLDS.cross_tenant_asset_denial
    );
    requireScenarioClean("cross_tenant_asset_denial", scenarios.cross_tenant_asset_denial);
    if (scenarios.cross_tenant_asset_denial.expected_denials !== BUYER_CAPACITY) {
      throw new Error("cross-tenant asset denial budget was not 1,000/1,000");
    }
  } catch (error) {
    testFailure = error;
  } finally {
    await cleanupStep(cleanupErrors, "product-create telemetry", async () => {
      productCreateTelemetry = await collectCapacityProductCreateTelemetry(
        bffTraceCollector,
        containers.api,
        scenarios.product_create !== undefined
      );
    });
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
      failurePhase = "teardown";
      testFailure = new Error("runtime_cleanup_failed");
    }
    if (!testFailure) {
      if (productCreateTelemetry.status !== "passed") {
        failurePhase = "product_create_telemetry";
        testFailure = new Error("product_create_telemetry_incomplete");
      }
    }
    if (!testFailure) {
      failurePhase = "threshold_validation";
      try {
        requireResourceThresholds(peaks);
      } catch (error) {
        thresholdFailures.push(`resources ${safeError(error)}`);
      }
    }
    if (!testFailure && thresholdFailures.length > 0) {
      testFailure = new Error(`capacity threshold failed: ${thresholdFailures.join("; ")}`);
    }
    const report = await buildReport(
      manifest,
      scenarios,
      peaks,
      containers,
      runtimeTeardown,
      warmupDurationMs,
      observedOwnerSessions,
      observedBuyerSessions,
      productCreateTelemetry,
      testFailure,
      failurePhase
    );
    await writeCapacityReport(report);
  }

    if (testFailure) throw new Error(safeError(testFailure));
  },
  CAPACITY_TEST_TIMEOUT_MS
);

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
    APPSELL_CAPACITY_PRODUCT_CREATE_TELEMETRY: "true",
    APPSELL_ENVIRONMENT: "ci",
    NEXT_TELEMETRY_DISABLED: "1"
  };
}

async function createSession(
  oauth: RedisOAuthService,
  port: number,
  subject: string,
  clientKey: string,
  timeoutMs = 5_000
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
    },
    timeoutMs
  );
  if (response.status !== 307) {
    throw new Error(`oauth_callback_status_${response.status}`);
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
  operation: (item: T) => Promise<{ status: number; expected: number; valid?: boolean }>,
  options?: {
    pauseAfter: number;
    betweenBatches: () => Promise<number>;
  }
): Promise<ScenarioResult> {
  const latencies: number[] = [];
  const statuses: Record<string, number> = {};
  let successes = 0;
  let expectedDenials = 0;
  let unexpectedErrors = 0;
  let validationFailures = 0;
  let timeouts = 0;
  const errors: string[] = [];
  const observations: RequestObservation[] = [];
  const batches = options
    ? [items.slice(0, options.pauseAfter), items.slice(options.pauseAfter)]
    : [items];
  let activeDuration = 0;
  let excludedWait = 0;
  let offset = 0;
  for (const [batchIndex, batch] of batches.entries()) {
    let cursor = 0;
    const started = performance.now();
    await Promise.all(
      Array.from({ length: Math.min(CONCURRENCY, batch.length) }, async () => {
        while (true) {
          const localIndex = cursor++;
          if (localIndex >= batch.length) return;
          const index = offset + localIndex;
          const itemStarted = performance.now();
          try {
            const result = await operation(batch[localIndex]!);
            const ended = performance.now();
            latencies.push(ended - itemStarted);
            observations.push({
              index,
              started_ms: itemStarted - DIAGNOSTIC_CLOCK_ORIGIN_MS,
              ended_ms: ended - DIAGNOSTIC_CLOCK_ORIGIN_MS,
              status: result.status,
              expected: result.expected,
              valid: result.valid ?? true,
              timeout: false
            });
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
            const ended = performance.now();
            const timedOut = /timeout|timed out|abort/i.test(safeError(error));
            latencies.push(ended - itemStarted);
            observations.push({
              index,
              started_ms: itemStarted - DIAGNOSTIC_CLOCK_ORIGIN_MS,
              ended_ms: ended - DIAGNOSTIC_CLOCK_ORIGIN_MS,
              status: null,
              expected: null,
              valid: null,
              timeout: timedOut
            });
            unexpectedErrors += 1;
            if (timedOut) timeouts += 1;
            errors.push(`request ${index}: ${safeError(error)}`);
          }
        }
      })
    );
    activeDuration += performance.now() - started;
    offset += batch.length;
    if (options && batchIndex < batches.length - 1 && batches[batchIndex + 1]!.length > 0) {
      excludedWait += await options.betweenBatches();
    }
  }
  const result: ScenarioResult = {
    requests: items.length,
    successes,
    expected_denials: expectedDenials,
    unexpected_errors: unexpectedErrors,
    validation_failures: validationFailures,
    timeouts,
    diagnostics: errors.slice(0, 5),
    statuses,
    duration_ms: round(activeDuration),
    excluded_wait_ms: round(excludedWait),
    throughput_per_second: round(items.length / Math.max(activeDuration / 1_000, 0.001)),
    latency_ms: {
      p50: percentile(latencies, 50),
      p95: percentile(latencies, 95),
      p99: percentile(latencies, 99),
      max: round(Math.max(...latencies, 0))
    },
    wave_diagnostics: aggregateRequestWaves(observations, CONCURRENCY)
  };
  return result;
}

function aggregateRequestWaves(
  observations: RequestObservation[],
  waveSize: number
): ScenarioWaveDiagnostic[] {
  if (!Number.isSafeInteger(waveSize) || waveSize <= 0) throw new Error("invalid_wave_size");
  const sorted = [...observations].sort((left, right) => left.index - right.index);
  const waves: ScenarioWaveDiagnostic[] = [];
  for (let offset = 0; offset < sorted.length; offset += waveSize) {
    const wave = sorted.slice(offset, offset + waveSize);
    const latencies = wave.map((observation) => observation.ended_ms - observation.started_ms);
    const start = Math.min(...wave.map((observation) => observation.started_ms));
    const end = Math.max(...wave.map((observation) => observation.ended_ms));
    const statuses: Record<string, number> = {};
    for (const observation of wave) {
      if (observation.status !== null) {
        statuses[String(observation.status)] = (statuses[String(observation.status)] ?? 0) + 1;
      }
    }
    waves.push({
      ordinal: waves.length + 1,
      start_ms: round(start),
      end_ms: round(end),
      elapsed_ms: round(end - start),
      requests: wave.length,
      responses: wave.filter((observation) => observation.status !== null).length,
      exceptions: wave.filter((observation) => observation.status === null).length,
      statuses,
      expected_matches: wave.filter((observation) =>
        observation.status !== null && observation.status === observation.expected
      ).length,
      validation_failures: wave.filter((observation) => observation.valid === false).length,
      timeouts: wave.filter((observation) => observation.timeout).length,
      latency_ms: {
        p95: percentile(latencies, 95),
        max: round(Math.max(...latencies, 0))
      }
    });
  }
  return waves;
}

test("aggregates sanitized request waves without changing scheduling", () => {
  const observations = Array.from({ length: 12 }, (_, index): RequestObservation => ({
    index,
    started_ms: index < 10 ? index : 100 + index,
    ended_ms: (index < 10 ? index : 100 + index) + 20 + index,
    status: index === 10 ? null : index === 11 ? 403 : 200,
    expected: index === 10 ? null : index === 11 ? 403 : 200,
    valid: index === 10 ? null : index !== 9,
    timeout: index === 10
  }));
  const waves = aggregateRequestWaves(observations.reverse(), 10);
  expect(waves).toEqual([
    {
      ordinal: 1,
      start_ms: 0,
      end_ms: 38,
      elapsed_ms: 38,
      requests: 10,
      responses: 10,
      exceptions: 0,
      statuses: { "200": 10 },
      expected_matches: 10,
      validation_failures: 1,
      timeouts: 0,
      latency_ms: { p95: 29, max: 29 }
    },
    {
      ordinal: 2,
      start_ms: 110,
      end_ms: 142,
      elapsed_ms: 32,
      requests: 2,
      responses: 1,
      exceptions: 1,
      statuses: { "403": 1 },
      expected_matches: 1,
      validation_failures: 0,
      timeouts: 1,
      latency_ms: { p95: 31, max: 31 }
    }
  ]);
  expect(Object.keys(waves[0]!)).not.toContain("ids");
  expect(Object.keys(waves[0]!)).not.toContain("payloads");
});

test("clamps host and BFF process-tree CPU diagnostics", () => {
  expect(clampPercent(-1)).toBe(0);
  expect(clampPercent(140)).toBe(100);
  expect(clampPercent(Number.NaN)).toBe(0);
  expect(hostCpuUtilizationPercent({ idle: 100, total: 200 }, { idle: 110, total: 220 })).toBe(50);
  expect(aggregateProcessTree([
    [10, 1, 100, 90],
    [11, 10, 50, 30],
    [12, 1, 500, 99]
  ], 10, 2)).toEqual({ rssBytes: 153_600, cpuPercent: 60 });
});

test("serializes and scans maximum cardinality diagnostics", async () => {
  const waves = Array.from({ length: 950 }, (_, index) => ({
    ordinal: index + 1,
    start_ms: index * 10,
    end_ms: index * 10 + 20,
    elapsed_ms: 20,
    requests: 10,
    responses: 10,
    exceptions: 0,
    statuses: { "200": 10 },
    expected_matches: 10,
    validation_failures: 0,
    timeouts: 0,
    latency_ms: { p95: 20, max: 20 }
  }));
  const samples = Array.from({ length: 3_601 }, (_, index): BffHostResourceSample => ({
    elapsed_ms: index * 250,
    phase: "scenario_buyer_library",
    process_tree_sample_valid: true,
    bff_cpu_percent: 50,
    host_cpu_percent: 50,
    load_average: { one_minute: 1, five_minutes: 1, fifteen_minutes: 1 }
  }));
  const serialized = JSON.stringify({
    schema_version: 2,
    scenarios: { maximum_cardinality_fixture: { wave_diagnostics: waves } },
    resources: { bff_host_samples: samples }
  });
  expect(waves).toHaveLength(950);
  expect(samples).toHaveLength(3_601);
  expect(Buffer.byteLength(serialized)).toBeLessThan(5 * 1024 * 1024);
  const directory = await mkdtemp(join(tmpdir(), "appsell-capacity-diagnostics-"));
  const artifact = join(directory, "capacity-100.json");
  try {
    await writeFile(artifact, serialized, { mode: 0o600 });
    const { stdout } = await execFileAsync(
      "python3",
      ["scripts/assert_capacity_artifact_safe.py", artifact],
      { timeout: 5_000 }
    );
    expect(stdout).toContain("capacity artifact scan: passed");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("capacity product-create telemetry is default-off and refuses remote environments", () => {
  expect(capacityProductCreateTelemetryEnabled({})).toBe(false);
  expect(capacityProductCreateTelemetryEnabled({
    APPSELL_CAPACITY_PRODUCT_CREATE_TELEMETRY: "true",
    APPSELL_ENVIRONMENT: "ci"
  })).toBe(true);
  expect(() => capacityProductCreateTelemetryEnabled({
    APPSELL_CAPACITY_PRODUCT_CREATE_TELEMETRY: "true",
    APPSELL_ENVIRONMENT: "staging"
  })).toThrow("capacity_product_create_telemetry_forbidden_environment");
  expect(() => capacityProductCreateTelemetryEnabled({
    APPSELL_CAPACITY_PRODUCT_CREATE_TELEMETRY: "true",
    APPSELL_ENVIRONMENT: "production"
  })).toThrow("capacity_product_create_telemetry_forbidden_environment");
  expect(() => capacityProductCreateTelemetryEnabled({
    APPSELL_CAPACITY_PRODUCT_CREATE_TELEMETRY: "true",
    APPSELL_ENVIRONMENT: "ci",
    NODE_ENV: "production"
  })).toThrow("capacity_product_create_telemetry_forbidden_environment");
});

test("capacity trace parser accepts only strict opaque JSON events", () => {
  expect(validCapacityProductCreateTrace("pc-000")).toBe(true);
  expect(validCapacityProductCreateTrace("pc-099")).toBe(true);
  for (const invalid of ["pc-100", "pc-00", "pc-001-extra", "tenant-001", "PC-001", ""]) {
    expect(validCapacityProductCreateTrace(invalid)).toBe(false);
  }
  expect(parseCapacityProductCreateTraceLine("ready on localhost")).toBeNull();
  expect(() => parseCapacityProductCreateTraceLine(JSON.stringify({
    ...capacityTraceFixture("bff", "pc-001"),
    url: "https://forbidden.example.test/tenant/1"
  }))).toThrow("rejected_capacity_trace_event");
});

test("capacity trace aggregate proves cardinality, scans safe and retains late counts", async () => {
  const bff = Array.from({ length: 100 }, (_, index) =>
    capacityTraceFixture("bff", `pc-${index.toString().padStart(3, "0")}`, index === 99 ? 5_001 : 10)
  );
  const api = Array.from({ length: 100 }, (_, index) =>
    capacityTraceFixture("api", `pc-${index.toString().padStart(3, "0")}`, index === 98 ? 5_100 : 8)
  );
  const aggregate = aggregateCapacityProductCreateTraces(bff, api);
  expect(aggregate.status).toBe("passed");
  expect(aggregate.matched_trace_cardinality).toBe(100);
  expect(aggregate.bff.late_completions).toBe(1);
  expect(aggregate.api.late_completions).toBe(1);
  expect(JSON.stringify(aggregate)).not.toContain("pc-000");
  expect(aggregateCapacityProductCreateTraces(bff.slice(0, 99), api).status).toBe("failed");
  const directory = await mkdtemp(join(tmpdir(), "appsell-capacity-traces-"));
  const artifact = join(directory, "telemetry.json");
  try {
    await writeFile(artifact, JSON.stringify(aggregate), { mode: 0o600 });
    const { stdout } = await execFileAsync(
      "python3",
      ["scripts/assert_capacity_artifact_safe.py", artifact],
      { timeout: 5_000 }
    );
    expect(stdout).toContain("capacity artifact scan: passed");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

function capacityTraceFixture(
  layer: "bff" | "api",
  trace: string,
  totalMs = 10
): CapacityProductCreateTrace {
  const stages: Record<string, number> = layer === "bff"
    ? { session_auth: 1, form_csrf: 1, domain_fetch: totalMs - 4, response: 2 }
    : {
        internal_auth_replay: 1,
        pool_checkout_apply_context: 1,
        sql: totalMs - 4,
        commit_response: 2
      };
  return {
    schema_version: 1,
    event: "appsell_capacity_product_create_trace",
    layer,
    trace,
    status: 201,
    outcome: "success",
    total_ms: totalMs,
    late_completion: totalMs >= 5_000,
    durations_ms: stages
  };
}

async function genericDenialResponse(
  response: Response,
  expectedError: string,
  forbiddenValues: string[]
) {
  const contentType = response.headers.get("content-type") ?? "";
  const cacheControl = response.headers.get("cache-control") ?? "";
  const body = await response.clone().text();
  let payload: unknown;
  try {
    payload = JSON.parse(body);
  } catch {
    return false;
  }
  return contentType.toLowerCase().startsWith("application/json") &&
    cacheControl.toLowerCase().split(/\s*,\s*/).some((directive) => directive === "no-store") &&
    typeof payload === "object" && payload !== null && !Array.isArray(payload) &&
    Object.keys(payload).length === 1 &&
    (payload as { error?: unknown }).error === expectedError &&
    forbiddenValues.every((value) => value.length > 0 && !body.includes(value));
}

function requireScenarioClean(name: string, result: ScenarioResult) {
  if (result.unexpected_errors > 0) {
    throw new Error(`${name} failed with ${result.unexpected_errors} unexpected errors`);
  }
}

function recordThreshold(
  failures: string[],
  name: string,
  result: ScenarioResult,
  threshold: ScenarioThreshold
) {
  const violations = [
    result.latency_ms.p95 > threshold.p95 ? `p95 ${result.latency_ms.p95}>${threshold.p95}` : "",
    result.latency_ms.p99 > threshold.p99 ? `p99 ${result.latency_ms.p99}>${threshold.p99}` : "",
    result.latency_ms.max > threshold.max ? `max ${result.latency_ms.max}>${threshold.max}` : "",
    result.throughput_per_second < threshold.throughput
      ? `throughput ${result.throughput_per_second}<${threshold.throughput}`
      : ""
  ].filter(Boolean);
  failures.push(...violations.map((violation) => `${name} ${violation}`));
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
  if (resources.process_tree_sample_errors > 0) {
    failures.push(`process-tree sampler errors: ${resources.process_tree_sample_errors}`);
  }
  const maxBffHostSamples = Math.ceil(CAPACITY_TEST_TIMEOUT_MS / 250) + 1;
  if (resources.bff_host_samples.length > maxBffHostSamples) {
    failures.push(`BFF host samples ${resources.bff_host_samples.length}>${maxBffHostSamples}`);
  }
  failures.push(...postgresHeadroomFailures({
    rawConnections: resources.postgres_connections,
    workloadConnections: resources.postgres_workload_connections,
    runtimeConnections: resources.postgres_runtime_connections,
    apiConnections: resources.postgres_api_connections,
    maxConnections: resources.postgres_max_connections,
    reservedConnections: resources.postgres_reserved_connections,
    sampleErrors: resources.postgres_sample_errors,
    maxSampleGapMs: resources.postgres_max_sample_gap_ms,
    activeWaitingConnections: resources.postgres_active_waiting,
    lockWaitingConnections: resources.postgres_lock_waiting,
    bufferPinWaitingConnections: resources.postgres_buffer_pin_waiting,
    otherWaitingConnections: resources.postgres_other_waiting,
    samplesWithActiveWaiting: resources.postgres_samples_with_active_waiting,
    samples: resources.samples
  }));
  if (resources.postgres_active_connections > 11) {
    failures.push(`Postgres active connections out of baseline: ${resources.postgres_active_connections}`);
  }
  if (failures.length > 0) throw new Error(`resource threshold failed: ${failures.join(", ")}`);
}

function startResourceSampler(
  bffPid: number,
  redisPid: number,
  containers: Record<string, string>,
  currentPhase: () => string
) {
  let running = true;
  const peaks: ResourcePeaks = {
    bff_rss_bytes: 0,
    redis_rss_bytes: 0,
    container_rss_bytes: Object.fromEntries(Object.keys(containers).map((name) => [name, 0])),
    postgres_connections: 0,
    postgres_workload_connections: 0,
    postgres_runtime_connections: 0,
    postgres_api_connections: 0,
    postgres_active_connections: 0,
    postgres_idle_connections: 0,
    postgres_active_waiting: 0,
    postgres_lock_waiting: 0,
    postgres_lwlock_waiting: 0,
    postgres_buffer_pin_waiting: 0,
    postgres_client_waiting: 0,
    postgres_io_waiting: 0,
    postgres_ipc_waiting: 0,
    postgres_activity_waiting: 0,
    postgres_timeout_waiting: 0,
    postgres_extension_waiting: 0,
    postgres_other_waiting: 0,
    postgres_samples_with_active_waiting: 0,
    postgres_samples_with_lock_waiting: 0,
    postgres_active_waiting_observed_ms: 0,
    postgres_wait_event_peaks: {},
    postgres_api_active_waiting: 0,
    postgres_api_wait_event_peaks: {},
    postgres_waiting_samples_by_phase: {},
    postgres_full_api_pool_waiting_samples: 0,
    postgres_full_api_pool_waiting_observed_ms: 0,
    postgres_max_consecutive_full_api_pool_waiting_samples: 0,
    postgres_max_consecutive_full_api_pool_waiting_ms: 0,
    postgres_sample_errors: 0,
    postgres_max_sample_gap_ms: 0,
    postgres_max_connections: 0,
    postgres_reserved_connections: 0,
    bff_cpu_percent: 0,
    host_cpu_percent: 0,
    process_tree_sample_errors: 0,
    bff_host_samples: [],
    samples: 0
  };
  let previousSampleStarted = 0;
  let previousHostCpu = hostCpuTimes();
  let waitDiagnostics = emptyPostgresWaitDiagnostics();
  const loop = (async () => {
    while (running) {
      const sampleStarted = performance.now();
      const sampleGap = previousSampleStarted > 0 ? sampleStarted - previousSampleStarted : 0;
      if (previousSampleStarted > 0) {
        peaks.postgres_max_sample_gap_ms = Math.max(
          peaks.postgres_max_sample_gap_ms,
          sampleGap
        );
      }
      previousSampleStarted = sampleStarted;
      const samplePhase = currentPhase();
      let processTrees: ProcessTreeSample;
      let containerSamples: ReadonlyArray<readonly [string, number]>;
      let connections: Awaited<ReturnType<typeof samplePostgresConnections>>;
      try {
        [processTrees, containerSamples, connections] = await Promise.all([
          sampleProcessTrees(bffPid, redisPid),
          Promise.all(
            Object.entries(containers).map(async ([name, id]) => [name, await containerRss(id)] as const)
          ),
          samplePostgresConnections(containers.postgres)
        ]);
      } catch {
        peaks.postgres_sample_errors += 1;
        running = false;
        continue;
      }
      const currentHostCpu = hostCpuTimes();
      const hostCpuPercent = hostCpuUtilizationPercent(previousHostCpu, currentHostCpu);
      previousHostCpu = currentHostCpu;
      const loads = loadavg();
      if (processTrees.valid) {
        peaks.bff_rss_bytes = Math.max(peaks.bff_rss_bytes, processTrees.bff.rssBytes);
        peaks.redis_rss_bytes = Math.max(peaks.redis_rss_bytes, processTrees.redis.rssBytes);
        peaks.bff_cpu_percent = Math.max(peaks.bff_cpu_percent, processTrees.bff.cpuPercent);
      } else {
        peaks.process_tree_sample_errors += 1;
      }
      peaks.host_cpu_percent = Math.max(peaks.host_cpu_percent, hostCpuPercent);
      peaks.bff_host_samples.push({
        elapsed_ms: round(sampleStarted - DIAGNOSTIC_CLOCK_ORIGIN_MS),
        phase: samplePhase,
        process_tree_sample_valid: processTrees.valid,
        bff_cpu_percent: processTrees.valid ? processTrees.bff.cpuPercent : null,
        host_cpu_percent: hostCpuPercent,
        load_average: {
          one_minute: round(loads[0] ?? 0),
          five_minutes: round(loads[1] ?? 0),
          fifteen_minutes: round(loads[2] ?? 0)
        }
      });
      for (const [name, bytes] of containerSamples) {
        peaks.container_rss_bytes[name] = Math.max(peaks.container_rss_bytes[name] ?? 0, bytes);
      }
      peaks.postgres_connections = Math.max(peaks.postgres_connections, connections.total);
      peaks.postgres_workload_connections = Math.max(
        peaks.postgres_workload_connections,
        connections.workload
      );
      peaks.postgres_runtime_connections = Math.max(
        peaks.postgres_runtime_connections,
        connections.runtime
      );
      peaks.postgres_api_connections = Math.max(
        peaks.postgres_api_connections,
        connections.api
      );
      peaks.postgres_active_connections = Math.max(
        peaks.postgres_active_connections,
        connections.active
      );
      peaks.postgres_idle_connections = Math.max(peaks.postgres_idle_connections, connections.idle);
      peaks.postgres_active_waiting = Math.max(
        peaks.postgres_active_waiting,
        connections.activeWaiting
      );
      peaks.postgres_lock_waiting = Math.max(
        peaks.postgres_lock_waiting,
        connections.lockWaiting
      );
      peaks.postgres_lwlock_waiting = Math.max(
        peaks.postgres_lwlock_waiting,
        connections.lwLockWaiting
      );
      peaks.postgres_buffer_pin_waiting = Math.max(
        peaks.postgres_buffer_pin_waiting,
        connections.bufferPinWaiting
      );
      peaks.postgres_client_waiting = Math.max(
        peaks.postgres_client_waiting,
        connections.clientWaiting
      );
      peaks.postgres_io_waiting = Math.max(
        peaks.postgres_io_waiting,
        connections.ioWaiting
      );
      peaks.postgres_ipc_waiting = Math.max(
        peaks.postgres_ipc_waiting,
        connections.ipcWaiting
      );
      peaks.postgres_activity_waiting = Math.max(
        peaks.postgres_activity_waiting,
        connections.activityWaiting
      );
      peaks.postgres_timeout_waiting = Math.max(
        peaks.postgres_timeout_waiting,
        connections.timeoutWaiting
      );
      peaks.postgres_extension_waiting = Math.max(
        peaks.postgres_extension_waiting,
        connections.extensionWaiting
      );
      peaks.postgres_other_waiting = Math.max(
        peaks.postgres_other_waiting,
        connections.otherWaiting
      );
      peaks.postgres_api_active_waiting = Math.max(
        peaks.postgres_api_active_waiting,
        connections.apiActiveWaiting
      );
      if (connections.activeWaiting > 0) {
        peaks.postgres_samples_with_active_waiting += 1;
        peaks.postgres_active_waiting_observed_ms += Math.max(0, sampleGap);
      }
      waitDiagnostics = accumulatePostgresWaitDiagnostics(
        waitDiagnostics,
        connections,
        samplePhase,
        sampleGap,
        CONCURRENCY
      );
      peaks.postgres_wait_event_peaks = waitDiagnostics.waitEventPeaks;
      peaks.postgres_api_wait_event_peaks = waitDiagnostics.apiWaitEventPeaks;
      peaks.postgres_waiting_samples_by_phase = waitDiagnostics.waitingSamplesByPhase;
      peaks.postgres_full_api_pool_waiting_samples =
        waitDiagnostics.fullApiPoolWaitingSamples;
      peaks.postgres_full_api_pool_waiting_observed_ms =
        waitDiagnostics.fullApiPoolWaitingObservedMs;
      peaks.postgres_max_consecutive_full_api_pool_waiting_samples =
        waitDiagnostics.maxConsecutiveFullApiPoolWaitingSamples;
      peaks.postgres_max_consecutive_full_api_pool_waiting_ms =
        waitDiagnostics.maxConsecutiveFullApiPoolWaitingMs;
      if (connections.lockWaiting > 0) peaks.postgres_samples_with_lock_waiting += 1;
      peaks.postgres_max_connections = connections.maxConnections;
      peaks.postgres_reserved_connections = connections.reservedConnections;
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
    postgres_workload_connections: 0,
    postgres_runtime_connections: 0,
    postgres_api_connections: 0,
    postgres_active_connections: 0,
    postgres_idle_connections: 0,
    postgres_active_waiting: 0,
    postgres_lock_waiting: 0,
    postgres_lwlock_waiting: 0,
    postgres_buffer_pin_waiting: 0,
    postgres_client_waiting: 0,
    postgres_io_waiting: 0,
    postgres_ipc_waiting: 0,
    postgres_activity_waiting: 0,
    postgres_timeout_waiting: 0,
    postgres_extension_waiting: 0,
    postgres_other_waiting: 0,
    postgres_samples_with_active_waiting: 0,
    postgres_samples_with_lock_waiting: 0,
    postgres_active_waiting_observed_ms: 0,
    postgres_wait_event_peaks: {},
    postgres_api_active_waiting: 0,
    postgres_api_wait_event_peaks: {},
    postgres_waiting_samples_by_phase: {},
    postgres_full_api_pool_waiting_samples: 0,
    postgres_full_api_pool_waiting_observed_ms: 0,
    postgres_max_consecutive_full_api_pool_waiting_samples: 0,
    postgres_max_consecutive_full_api_pool_waiting_ms: 0,
    postgres_sample_errors: 0,
    postgres_max_sample_gap_ms: 0,
    postgres_max_connections: 0,
    postgres_reserved_connections: 0,
    bff_cpu_percent: 0,
    host_cpu_percent: 0,
    process_tree_sample_errors: 0,
    bff_host_samples: [],
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

async function sampleProcessTrees(bffPid: number, redisPid: number) {
  try {
    const { stdout } = await execFileAsync(
      "ps",
      ["-axo", "pid=,ppid=,rss=,%cpu="],
      { timeout: 3_000 }
    );
    const rows = stdout.trim().split("\n").map((line) => line.trim().split(/\s+/).map(Number));
    const logicalCpuCount = Math.max(cpus().length, 1);
    return {
      valid: true,
      bff: aggregateProcessTree(rows, bffPid, logicalCpuCount),
      redis: aggregateProcessTree(rows, redisPid, logicalCpuCount)
    };
  } catch {
    return {
      valid: false,
      bff: { rssBytes: 0, cpuPercent: 0 },
      redis: { rssBytes: 0, cpuPercent: 0 }
    };
  }
}

function aggregateProcessTree(
  rows: number[][],
  rootPid: number,
  logicalCpuCount: number
): ProcessTreeStats {
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
  const totals = rows.reduce((aggregate, [pid, , rss, cpu]) => {
    if (!selected.has(pid!)) return aggregate;
    return {
      rssBytes: aggregate.rssBytes + (Number.isFinite(rss) ? rss! * 1024 : 0),
      cpuPercent: aggregate.cpuPercent + (Number.isFinite(cpu) ? cpu! : 0)
    };
  }, { rssBytes: 0, cpuPercent: 0 });
  return {
    ...totals,
    cpuPercent: clampPercent(totals.cpuPercent / Math.max(logicalCpuCount, 1))
  };
}

function hostCpuTimes() {
  return cpus().reduce((aggregate, cpu) => {
    const total = Object.values(cpu.times).reduce((sum, value) => sum + value, 0);
    return { idle: aggregate.idle + cpu.times.idle, total: aggregate.total + total };
  }, { idle: 0, total: 0 });
}

function hostCpuUtilizationPercent(
  previous: { idle: number; total: number },
  current: { idle: number; total: number }
) {
  const totalDelta = current.total - previous.total;
  const idleDelta = current.idle - previous.idle;
  return clampPercent(totalDelta > 0 ? ((totalDelta - idleDelta) / totalDelta) * 100 : 0);
}

function clampPercent(value: number) {
  return round(Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : 0);
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

async function buildReport(
  manifest: FixtureManifest,
  scenarios: Record<string, ScenarioResult>,
  resources: ResourcePeaks,
  containers: Record<string, string>,
  runtimeTeardown: Record<string, unknown>,
  warmupDurationMs: number,
  observedOwnerSessions: number,
  observedBuyerSessions: number,
  productCreateTelemetry: CapacityProductCreateTelemetryAggregate,
  failure: unknown,
  failurePhase: string
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
  const crossTenant2xx = [
    "buyer_product_denial",
    "cross_tenant_player_denial",
    "cross_tenant_denial",
    "cross_tenant_asset_denial",
    "cross_tenant_progress_denial"
  ].reduce(
    (scenarioSum, name) => scenarioSum + Object.entries(scenarios[name]?.statuses ?? {}).reduce(
      (sum, [status, count]) =>
        sum + (Number(status) >= 200 && Number(status) < 300 ? count : 0),
      0
    ),
    0
  );
  const workerRss = Number(requiredEnvironment("APPSELL_CAPACITY_STORAGE_VERTICAL_TREE_RSS_BYTES"));
  const startup = JSON.parse(
    await readFile(requiredEnvironment("APPSELL_CAPACITY_STARTUP_REPORT"), "utf8")
  ) as {
    status?: string;
    calibration_eligible?: boolean;
    recovered?: boolean;
    fault_injection?: { occurred?: boolean };
  };
  const postgresHeadroom = calculatePostgresHeadroom({
    rawConnections: resources.postgres_connections,
    workloadConnections: resources.postgres_workload_connections,
    runtimeConnections: resources.postgres_runtime_connections,
    apiConnections: resources.postgres_api_connections,
    maxConnections: resources.postgres_max_connections,
    reservedConnections: resources.postgres_reserved_connections,
    sampleErrors: resources.postgres_sample_errors,
    maxSampleGapMs: resources.postgres_max_sample_gap_ms
  });
  const failureCode = failure ? safeError(failure) : null;
  const cleanupFailed = Array.isArray(runtimeTeardown.cleanup_errors) &&
    runtimeTeardown.cleanup_errors.length > 0;
  return {
    schema_version: 2,
    status: failure ? "failed" : "passed",
    mode: "local_ci",
    production_certified: false,
    calibration_eligible:
      !failure &&
      startup.status === "passed" &&
      startup.calibration_eligible === true &&
      sourceEvidence.dirty === false &&
      sourceEvidence.changed_file_count === 0,
    phases: {
      postgres_startup: {
        status: startup.status === "passed" ? "passed" : "failed",
        recovered: startup.recovered === true,
        fault_injected: startup.fault_injection?.occurred === true
      },
      workload: {
        status: failureCode === "workload_failed" ? "failed" : failure ? "not_certified" : "passed"
      },
      thresholds: {
        status: failureCode === "threshold_failed" ? "failed" : failure ? "not_certified" : "passed"
      },
      teardown: { status: cleanupFailed ? "failed" : "passed" }
    },
    generated_at: new Date().toISOString(),
    git_sha: process.env.GITHUB_SHA || sourceEvidence.git_sha,
    source_evidence: sourceEvidence,
    tenant_count: manifest.tenant_count,
    admin_role: "seller_owner",
    admin_accounts: manifest.admin_count,
    buyers_per_admin: manifest.buyers_per_tenant,
    buyer_accounts: manifest.buyer_count,
    entitlement_count: manifest.buyer_count,
    distinct_subjects: manifest.distinct_subjects,
    owner_sessions: observedOwnerSessions,
    buyer_sessions: observedBuyerSessions,
    owner_sessions_observed: observedOwnerSessions,
    buyer_sessions_observed: observedBuyerSessions,
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
    product_create_telemetry: productCreateTelemetry,
    resources: {
      ...resources,
      storage_vertical_process_tree_rss_bytes: workerRss,
      postgres_usable_connections: postgresHeadroom.usableConnections,
      postgres_headroom_connections: postgresHeadroom.headroomConnections,
      postgres_headroom_percent: postgresHeadroom.headroomPercent
    },
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
      ...Object.fromEntries(
        Object.entries(SCENARIO_THRESHOLDS).map(([name, threshold]) => [
          name,
          {
            p95_ms: threshold.p95,
            p99_ms: threshold.p99,
            max_ms: threshold.max,
            min_requests_per_second: threshold.throughput
          }
        ])
      ),
      max_postgres_raw_connections: 13,
      max_postgres_workload_connections: 12,
      max_postgres_runtime_connections: 10,
      postgres_server_max_connections: 24,
      postgres_reserved_connections: 3,
      min_postgres_headroom_connections: 9,
      min_postgres_headroom_percent: 40,
      max_postgres_active_waiting_connections: 5,
      max_postgres_lock_waiting_connections: 0,
      max_postgres_buffer_pin_waiting_connections: 0,
      max_postgres_unclassified_waiting_connections: 0,
      max_postgres_waiting_sample_ratio: 0.25,
      oauth_default_max_global_per_action_per_window: 1_000,
      oauth_rate_window_ms: OAUTH_RATE_WINDOW_MS,
      oauth_first_window_capacity: OAUTH_FIRST_WINDOW_CAPACITY,
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
      bff_runtime_scope: "next_dev_webpack_local_ci_not_production_runtime",
      bff_node_max_old_space_mb: 768,
      account_cardinality_scope: "100_synthetic_owners_and_1000_synthetic_buyers_local_ci",
      identity_scope: "synthetic_subjects_not_real_identity_accounts",
      concurrency_scope: "10_concurrent_requests_not_1100_simultaneous_users",
      diagnostic_clock_scope: "monotonic_capacity_executor_origin_shared_by_waves_and_resource_samples",
      wave_diagnostics_scope: "index_cohorts_of_10_observations_may_overlap_in_time_no_scheduling_barrier",
      bff_cpu_scope: "auxiliary_ps_lifetime_average_process_tree_normalized_by_logical_cpus_0_to_100",
      max_bff_host_samples: Math.ceil(CAPACITY_TEST_TIMEOUT_MS / 250) + 1,
      executor_timeout_ms: CAPACITY_TEST_TIMEOUT_MS,
      outer_executor_timeout_seconds: Number(
        requiredEnvironment("APPSELL_CAPACITY_OUTER_TIMEOUT_SECONDS")
      ),
      oauth_rate_limit_scope: "runtime_default_1000_per_action_per_60s_across_two_windows",
      asset_session_scope: "1000_buyer_sessions_across_100_tenants_through_bff",
      s3_vertical_scope: "single_representative_tenant_direct_worker_and_storage",
      database_topology: "one_api_process_with_inline_worker_during_capacity_workload",
      dedicated_worker_concurrent_during_capacity_workload: false,
      postgres_server_budget_scope: "disposable_local_ci_only_not_production_sizing",
      high_availability: false,
      staging_load_2x: false,
      vps_sizing: false
    },
    postgres_startup: startup,
    failure: failureCode,
    failure_phase: failure ? failurePhase : null
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
    postgres_headroom_connections: number;
    postgres_headroom_percent: number;
  };
  const markdown = [
    "# AppSell capacity baseline — 100 admins and 1,000 buyers",
    "",
    `- Status: **${String(report.status)}**`,
    `- Mode: Local/CI`,
    `- Tenants: ${String(report.tenant_count)}`,
    `- Admin accounts (seller_owner): ${String(report.admin_accounts)}`,
    `- Buyers per admin: ${String(report.buyers_per_admin)}`,
    `- Buyer accounts: ${String(report.buyer_accounts)}`,
    `- Owner sessions observed: ${String(report.owner_sessions_observed)}`,
    `- Buyer sessions observed: ${String(report.buyer_sessions_observed)}`,
    `- Distinct OAuth subjects: ${String(report.distinct_subjects)}`,
    `- Concurrency: ${String(report.concurrency)}`,
    `- OAuth rate-window wait excluded from active throughput: ${String((report.scenarios as Record<string, ScenarioResult>).oauth_callback_and_sync?.excluded_wait_ms ?? 0)} ms`,
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
    `- Postgres raw connections: ${resources.postgres_connections}`,
    `- Postgres workload connections (sampler excluded): ${resources.postgres_workload_connections}`,
    `- Postgres runtime/API connections: ${resources.postgres_runtime_connections}/${resources.postgres_api_connections}`,
    `- Postgres headroom: ${resources.postgres_headroom_connections} slots (${resources.postgres_headroom_percent}%)`,
    `- Postgres lock waits: ${resources.postgres_lock_waiting}`,
    `- Postgres LWLock wait peak: ${resources.postgres_lwlock_waiting}`,
    `- Postgres BufferPin waits: ${resources.postgres_buffer_pin_waiting}`,
    `- Postgres wait peaks (Client/IO/IPC/Activity/Timeout/Extension/Other): ${resources.postgres_client_waiting}/${resources.postgres_io_waiting}/${resources.postgres_ipc_waiting}/${resources.postgres_activity_waiting}/${resources.postgres_timeout_waiting}/${resources.postgres_extension_waiting}/${resources.postgres_other_waiting}`,
    `- Postgres active-wait samples: ${resources.postgres_samples_with_active_waiting}/${resources.samples}`,
    `- Postgres exact wait-event peaks: ${JSON.stringify(resources.postgres_wait_event_peaks)}`,
    `- Postgres API-pool active-wait peak: ${resources.postgres_api_active_waiting}`,
    `- Postgres API-pool exact wait-event peaks: ${JSON.stringify(resources.postgres_api_wait_event_peaks)}`,
    `- Postgres waiting samples by phase: ${JSON.stringify(resources.postgres_waiting_samples_by_phase)}`,
    `- Postgres full API-pool waiting: ${resources.postgres_full_api_pool_waiting_samples} samples / ${round(resources.postgres_full_api_pool_waiting_observed_ms)} ms between consecutive positive samples`,
    `- Postgres max consecutive full API-pool waiting: ${resources.postgres_max_consecutive_full_api_pool_waiting_samples} samples / ${round(resources.postgres_max_consecutive_full_api_pool_waiting_ms)} ms between consecutive positive samples`,
    "",
    "These are synthetic Local/CI subjects with concurrency 10, not 1,100 simultaneous or real Identity accounts. This artifact is not VPS, staging, HA, restore, ClamAV, billing, DNS or production evidence.",
    ""
  ].join("\n");
  await writeFile(output.replace(/\.json$/, ".md"), markdown, { encoding: "utf8" });
}

function assertFixture(manifest: FixtureManifest) {
  if (
    manifest.schema_version !== 2 || manifest.mode !== "local_ci" ||
    manifest.tenant_count !== TENANT_CAPACITY ||
    manifest.admin_count !== TENANT_CAPACITY ||
    manifest.buyers_per_tenant !== BUYERS_PER_TENANT ||
    manifest.buyer_count !== BUYER_CAPACITY ||
    manifest.distinct_subjects !== TENANT_CAPACITY + BUYER_CAPACITY ||
    manifest.tenants.length !== TENANT_CAPACITY ||
    manifest.tenants.some((tenant) => tenant.buyers.length !== BUYERS_PER_TENANT)
  ) {
    throw new Error("invalid capacity fixture manifest");
  }
  const sellers = new Set(manifest.tenants.map((tenant) => tenant.seller_id));
  const products = new Set(manifest.tenants.map((tenant) => tenant.product_id));
  const buyers = manifest.tenants.flatMap((tenant) => tenant.buyers);
  const subjects = new Set([
    ...manifest.tenants.map((tenant) => tenant.owner_subject),
    ...buyers.map((buyer) => buyer.subject)
  ]);
  const buyerIdentities = new Set(buyers.map((buyer) => buyer.identity_id));
  const entitlements = new Set(buyers.map((buyer) => buyer.entitlement_id));
  if (
    sellers.size !== TENANT_CAPACITY || products.size !== TENANT_CAPACITY ||
    subjects.size !== TENANT_CAPACITY + BUYER_CAPACITY ||
    buyerIdentities.size !== BUYER_CAPACITY || entitlements.size !== BUYER_CAPACITY
  ) {
    throw new Error("capacity fixture does not contain distinct tenants and subjects");
  }
}

function buyerSessionKey(tenantOrdinal: number, buyerOrdinal: number) {
  return `${tenantOrdinal}:${buyerOrdinal}`;
}

function requiredSession<K, T>(sessions: Map<K, T>, key: K, role: string) {
  const value = sessions.get(key);
  if (!value) throw new Error(`missing ${role} session`);
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

async function waitForNextOauthRateWindow() {
  const elapsedInWindow = Date.now() % OAUTH_RATE_WINDOW_MS;
  const waitMs = OAUTH_RATE_WINDOW_MS - elapsedInWindow + 100;
  await new Promise((resolve) => setTimeout(resolve, waitMs));
  return waitMs;
}

async function requireStatus(responsePromise: Promise<Response>, expected: number, label: string) {
  const response = await responsePromise;
  if (response.status !== expected) {
    throw new Error(`${label.replace(/[^a-z0-9]+/gi, "_").toLowerCase()}_status_${response.status}`);
  }
}

function requiredEnvironment(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`Missing capacity test setting: ${name}`);
  return value;
}

function cookiePair(setCookie: string | null, name: string) {
  const match = setCookie?.match(new RegExp(`(?:^|,\\s*)(${name}=[^;]+)`));
  if (!match) throw new Error(`Missing ${name} cookie.`);
  return match[1] ?? "";
}

function safeError(error: unknown) {
  if (!(error instanceof Error)) return "unexpected_error";
  const message = error.message.toLowerCase();
  if (/timeout|timed out|abort/.test(message)) return "request_timeout";
  if (message.includes("threshold")) return "threshold_failed";
  if (message.includes("cleanup")) return "teardown_failed";
  if (message.startsWith("oauth_callback_status_")) return "oauth_callback_failed";
  if (message.includes("_status_")) return "unexpected_http_status";
  return "workload_failed";
}

function startBffCapacityTraceCollector(process: ChildProcess) {
  let buffer = "";
  const events: CapacityProductCreateTrace[] = [];
  let rejected = 0;
  const consumeLine = (line: string) => {
    if (!line.trim()) return;
    try {
      const event = parseCapacityProductCreateTraceLine(line.trim());
      if (event) {
        if (event.layer === "bff") events.push(event);
        else rejected += 1;
      }
    } catch {
      rejected += 1;
    }
  };
  process.stdout?.on("data", (chunk) => {
    buffer += String(chunk);
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() ?? "";
    for (const line of lines) consumeLine(line);
  });
  return {
    snapshot: () => ({ events: [...events], rejected }),
    flush: () => {
      consumeLine(buffer);
      buffer = "";
      return { events: [...events], rejected };
    }
  };
}

async function collectCapacityProductCreateTelemetry(
  bffCollector: ReturnType<typeof startBffCapacityTraceCollector> | undefined,
  apiContainerId: string | undefined,
  scenarioStarted: boolean
) {
  if (!bffCollector || !apiContainerId) {
    return aggregateCapacityProductCreateTraces([], [], 0, true);
  }
  const deadline = Date.now() + (scenarioStarted ? CAPACITY_TRACE_DRAIN_TIMEOUT_MS : 0);
  let apiEvents: CapacityProductCreateTrace[] = [];
  let apiRejected = 0;
  do {
    const { stdout, stderr } = await execFileAsync(
      "docker",
      ["logs", apiContainerId],
      { timeout: 5_000, maxBuffer: 20 * 1024 * 1024 }
    );
    ({ events: apiEvents, rejected: apiRejected } = parseApiCapacityTraceLogs(
      `${stdout}\n${stderr}`
    ));
    if (!scenarioStarted ||
        (bffCollector.snapshot().events.length >= TENANT_CAPACITY &&
         apiEvents.length >= TENANT_CAPACITY)) break;
    await delay(100);
  } while (Date.now() < deadline);
  const bff = bffCollector.flush();
  return aggregateCapacityProductCreateTraces(
    bff.events,
    apiEvents,
    bff.rejected + apiRejected,
    true
  );
}

function parseApiCapacityTraceLogs(content: string) {
  const events: CapacityProductCreateTrace[] = [];
  let rejected = 0;
  for (const raw of content.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    try {
      const event = parseCapacityProductCreateTraceLine(line);
      if (event) {
        if (event.layer === "api") events.push(event);
        else rejected += 1;
      }
    } catch {
      rejected += 1;
    }
  }
  return { events, rejected };
}

// Kept below the test helpers so the spawned process cannot accidentally inherit
// fixture paths or database superuser credentials not explicitly provided.
function startBff(port: number, distDir: string, shared: Record<string, string>) {
  return spawn(
    process.execPath,
    [
      join(process.cwd(), "node_modules/next/dist/bin/next"),
      "dev",
      "--webpack",
      "-H",
      "127.0.0.1",
      "-p",
      String(port)
    ],
    {
      cwd: process.cwd(),
      env: {
        PATH: process.env.PATH,
        HOME: process.env.HOME,
        NODE_ENV: "development",
        NODE_OPTIONS: "--max-old-space-size=768",
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
