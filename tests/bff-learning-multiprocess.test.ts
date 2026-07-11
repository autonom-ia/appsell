import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes, randomUUID } from "node:crypto";
import { readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { expect, test } from "vitest";

import { startIsolatedRedis, unusedNonDefaultPort } from "./helpers/isolated-redis";

const domainApiUrl = process.env.APPSELL_TEST_DOMAIN_API_URL;

test.skipIf(!domainApiUrl)("persists fail-closed learning progress across two real BFFs", async () => {
  const generatedFiles = ["next-env.d.ts", "tsconfig.json"];
  const originals = await Promise.all(
    generatedFiles.map((file) => readFile(join(process.cwd(), file)))
  );
  const redis = await startIsolatedRedis();
  const firstPort = await unusedNonDefaultPort();
  const secondPort = await unusedNonDefaultPort();
  const suffix = `${process.pid}-${Date.now()}`;
  const firstDist = `.next-learning-${suffix}-one`;
  const secondDist = `.next-learning-${suffix}-two`;
  const publicOrigin = `http://localhost:${firstPort}`;
  const shared = {
    APPSELL_REDIS_URL: redis.url,
    APPSELL_REDIS_PREFIX: `appsell:test:learning-${suffix}`,
    APPSELL_SESSION_HMAC_KEY_B64: randomBytes(32).toString("base64"),
    APPSELL_CSRF_HMAC_KEY_B64: randomBytes(32).toString("base64"),
    APPSELL_PUBLIC_ORIGIN: publicOrigin,
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
    APPSELL_PRIVATE_LEARNING_ASSET_ROOT: join(process.cwd(), "output", "pdf", "codex"),
    APPSELL_DEMO_ENABLED: "true",
    NEXT_TELEMETRY_DISABLED: "1"
  };
  const first = startBff(firstPort, firstDist, shared);
  const second = startBff(secondPort, secondDist, shared);

  try {
    await Promise.all([waitForBff(first, firstPort), waitForBff(second, secondPort)]);
    const buyer = await login(publicOrigin, "buyer");
    const admin = await login(publicOrigin, "admin");
    const firstOrigin = `http://127.0.0.1:${firstPort}`;
    const secondOrigin = `http://127.0.0.1:${secondPort}`;
    const library = await fetch(`${firstOrigin}/api/library`, {
      headers: { cookie: buyer.session }
    });
    expect(library.status, await library.clone().text()).toBe(200);
    const libraryPayload = await library.json() as {
      items: Array<{ entitlement_id: string; resume_lesson_id: string }>;
    };
    const item = libraryPayload.items[0]!;
    const playerUrl = `/api/library/items/${item.entitlement_id}/lessons/${item.resume_lesson_id}`;
    const playerResponse = await fetch(`${secondOrigin}${playerUrl}`, {
      headers: { cookie: buyer.session }
    });
    expect(playerResponse.status, await playerResponse.clone().text()).toBe(200);
    const playerEnvelope = await playerResponse.json() as {
      player: {
        state_version: number;
        selected_lesson_id: string;
        selected_asset: { href: string };
      };
      csrf: { progress: string; complete: string };
    };
    expect(JSON.stringify(playerEnvelope)).not.toContain(cookieValue(buyer.session));
    const assetPath = playerEnvelope.player.selected_asset.href;
    expect(assetPath).toMatch(
      new RegExp(`^/api/library/items/${item.entitlement_id}/assets/[0-9a-f-]{36}$`)
    );
    for (const bffOrigin of [firstOrigin, secondOrigin]) {
      const assetResponse = await fetch(`${bffOrigin}${assetPath}`, {
        headers: { cookie: buyer.session }
      });
      expect(assetResponse.status, await assetResponse.clone().text()).toBe(200);
      expect(assetResponse.headers.get("content-type")).toBe("application/pdf");
      expect(assetResponse.headers.get("cache-control")).toBe("private, no-store, max-age=0");
      expect(Buffer.from(await assetResponse.arrayBuffer()).subarray(0, 5).toString()).toBe("%PDF-");
    }
    const adminAsset = await fetch(`${firstOrigin}${assetPath}`, {
      headers: { cookie: admin.session }
    });
    expect(adminAsset.status).toBe(404);
    const lessonId = playerEnvelope.player.selected_lesson_id;
    const progressPath = `/api/library/items/${item.entitlement_id}/lessons/${lessonId}/progress`;
    const idempotencyKey = randomUUID();
    const fields = {
      _csrf: playerEnvelope.csrf.progress,
      idempotency_key: idempotencyKey,
      progress_percent: "37",
      expected_version: String(playerEnvelope.player.state_version)
    };

    const firstWrite = await mutate(firstOrigin, progressPath, buyer.session, publicOrigin, fields);
    expect(firstWrite.status, await firstWrite.clone().text()).toBe(200);
    const firstResult = await firstWrite.json() as { progress: { state_version: number; applied: boolean } };
    expect(firstResult.progress.applied).toBe(true);

    const replay = await mutate(secondOrigin, progressPath, buyer.session, publicOrigin, fields);
    expect(replay.status, await replay.clone().text()).toBe(200);
    expect(await replay.json()).toMatchObject({
      progress: { state_version: firstResult.progress.state_version, applied: false }
    });

    const conflict = await mutate(secondOrigin, progressPath, buyer.session, publicOrigin, {
      ...fields,
      progress_percent: "38"
    });
    expect(conflict.status).toBe(409);
    expect(await conflict.json()).toEqual({ error: "idempotency_conflict" });

    for (const origin of [undefined, "https://attacker.invalid"]) {
      const rejected = await mutate(firstOrigin, progressPath, buyer.session, origin, {
        ...fields,
        idempotency_key: randomUUID(),
        expected_version: String(firstResult.progress.state_version)
      });
      expect(rejected.status).toBe(403);
      expect(await rejected.json()).toEqual({ error: "forbidden_mutation" });
    }
    const wrongAction = await mutate(firstOrigin, progressPath, buyer.session, publicOrigin, {
      ...fields,
      _csrf: playerEnvelope.csrf.complete,
      idempotency_key: randomUUID(),
      expected_version: String(firstResult.progress.state_version)
    });
    expect(wrongAction.status).toBe(403);

    const unchanged = await fetch(`${secondOrigin}${playerUrl}`, {
      headers: { cookie: buyer.session }
    });
    expect(unchanged.status).toBe(200);
    expect(await unchanged.json()).toMatchObject({
      player: { state_version: firstResult.progress.state_version }
    });
  } finally {
    await Promise.all([stopBff(first), stopBff(second)]);
    await redis.stop();
    await Promise.all([
      rm(join(process.cwd(), firstDist), { recursive: true, force: true }),
      rm(join(process.cwd(), secondDist), { recursive: true, force: true })
    ]);
    await Promise.all(
      generatedFiles.map((file, index) => writeFile(join(process.cwd(), file), originals[index] ?? ""))
    );
  }
}, 90_000);

async function mutate(
  origin: string,
  path: string,
  cookie: string,
  requestOrigin: string | undefined,
  fields: Record<string, string>
) {
  const headers: Record<string, string> = {
    cookie,
    "content-type": "application/x-www-form-urlencoded"
  };
  if (requestOrigin !== undefined) headers.origin = requestOrigin;
  return fetch(`${origin}${path}`, {
    method: "PUT",
    headers,
    body: new URLSearchParams(fields)
  });
}

async function login(origin: string, persona: "buyer" | "admin") {
  const start = await fetch(`${origin}/auth/login?persona=${persona}`, { redirect: "manual" });
  const transaction = cookiePair(start.headers.get("set-cookie"), "appsell_oauth_tx");
  const authorize = await fetch(requiredHeader(start, "location"), { redirect: "manual" });
  const callback = await fetch(requiredHeader(authorize, "location"), {
    redirect: "manual",
    headers: { cookie: transaction }
  });
  expect(callback.status, await callback.clone().text()).toBe(307);
  return { session: cookiePair(callback.headers.get("set-cookie"), "appsell_session") };
}

function startBff(port: number, distDir: string, shared: Record<string, string>) {
  return spawn(
    process.execPath,
    [join(process.cwd(), "node_modules/next/dist/bin/next"), "dev", "-H", "127.0.0.1", "-p", String(port)],
    {
      cwd: process.cwd(),
      env: { ...process.env, ...shared, APPSELL_NEXT_DIST_DIR: distDir },
      stdio: ["ignore", "pipe", "pipe"]
    }
  );
}

async function waitForBff(process: ChildProcess, port: number) {
  const deadline = Date.now() + 25_000;
  let diagnostics = "";
  process.stderr?.on("data", (chunk) => {
    diagnostics = `${diagnostics}${String(chunk)}`.slice(-3_000);
  });
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

function cookieValue(cookie: string) {
  return cookie.slice(cookie.indexOf("=") + 1);
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
  await Promise.race([exited, new Promise<void>((resolve) => setTimeout(resolve, 3_000))]);
  if (process.exitCode === null) {
    process.kill("SIGKILL");
    await exited;
  }
}
