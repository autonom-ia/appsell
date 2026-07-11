import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes, randomUUID } from "node:crypto";
import { readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { expect, test } from "vitest";

import { deriveCsrfToken } from "../lib/csrf";
import { startIsolatedRedis, unusedNonDefaultPort } from "./helpers/isolated-redis";

const domainApiUrl = process.env.APPSELL_TEST_DOMAIN_API_URL;

test.skipIf(!domainApiUrl)("two BFFs persist seller onboarding through the real API and PostgreSQL", async () => {
  const generatedFiles = ["next-env.d.ts", "tsconfig.json"];
  const originals = await Promise.all(generatedFiles.map((file) => readFile(join(process.cwd(), file))));
  const redis = await startIsolatedRedis();
  const firstPort = await unusedNonDefaultPort();
  const secondPort = await unusedNonDefaultPort();
  const suffix = `${process.pid}-${Date.now()}`;
  const firstDist = `.next-domain-${suffix}-one`;
  const secondDist = `.next-domain-${suffix}-two`;
  const csrfKey = randomBytes(32);
  const shared = {
    APPSELL_REDIS_URL: redis.url,
    APPSELL_REDIS_PREFIX: `appsell:test:domain-${suffix}`,
    APPSELL_SESSION_HMAC_KEY_B64: randomBytes(32).toString("base64"),
    APPSELL_CSRF_HMAC_KEY_B64: csrfKey.toString("base64"),
    APPSELL_PUBLIC_ORIGIN: `http://localhost:${firstPort}`,
    APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: JSON.stringify([
      { id: "test-v1", key: randomBytes(32).toString("base64"), active: true }
    ]),
    APPSELL_DOMAIN_API_URL: requiredEnvironment("APPSELL_TEST_DOMAIN_API_URL"),
    APPSELL_INTERNAL_ISSUER: requiredEnvironment("APPSELL_INTERNAL_ISSUER"),
    APPSELL_INTERNAL_AUDIENCE: requiredEnvironment("APPSELL_INTERNAL_AUDIENCE"),
    APPSELL_IDENTITY_ISSUER: requiredEnvironment("APPSELL_IDENTITY_ISSUER"),
    APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON: requiredEnvironment("APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON"),
    APPSELL_SERVICE_CREDENTIAL_B64: requiredEnvironment("APPSELL_SERVICE_CREDENTIAL_B64"),
    APPSELL_LOCAL_IDENTITY_EMAIL: requiredEnvironment("APPSELL_LOCAL_IDENTITY_EMAIL"),
    NEXT_TELEMETRY_DISABLED: "1"
  };
  const first = startBff(firstPort, firstDist, shared);
  const second = startBff(secondPort, secondDist, shared);

  try {
    await Promise.all([waitForBff(first, firstPort), waitForBff(second, secondPort)]);
    const login = await fetch(`http://127.0.0.1:${firstPort}/auth/login`, { redirect: "manual" });
    const transaction = cookiePair(login.headers.get("set-cookie"), "appsell_oauth_tx");
    const authorize = await fetch(requiredHeader(login, "location"), { redirect: "manual" });
    const callbackUrl = new URL(requiredHeader(authorize, "location"));
    callbackUrl.hostname = "127.0.0.1";
    callbackUrl.port = String(secondPort);
    const callback = await fetch(callbackUrl, {
      redirect: "manual",
      headers: { cookie: transaction }
    });
    expect(callback.status).toBe(307);
    const setCookie = callback.headers.get("set-cookie");
    const session = cookiePair(setCookie, "appsell_session");
    const sellerCsrf = cookiePair(setCookie, "appsell_seller_csrf");
    const sessionId = session.slice(session.indexOf("=") + 1);
    const sellerCsrfToken = sellerCsrf.slice(sellerCsrf.indexOf("=") + 1);
    expect(sellerCsrfToken).toBe(deriveCsrfToken(sessionId, "POST:/api/sellers", csrfKey));
    const cookie = `${session}; ${sellerCsrf}`;

    const empty = await fetch(`http://127.0.0.1:${firstPort}/api/sellers`, {
      headers: { cookie: session }
    });
    expect(empty.status).toBe(200);
    expect(await empty.json()).toEqual({ sellers: [] });

    const slug = `studio-${randomUUID().slice(0, 8)}`;
    const onboarded = await fetch(`http://127.0.0.1:${secondPort}/api/sellers`, {
      method: "POST",
      headers: {
        cookie,
        origin: `http://localhost:${firstPort}`
      },
      body: new URLSearchParams({
        _csrf: sellerCsrfToken,
        idempotency_key: randomUUID(),
        slug,
        display_name: "Studio Integrado"
      })
    });
    expect(onboarded.status, await onboarded.clone().text()).toBe(201);

    const persisted = await fetch(`http://127.0.0.1:${firstPort}/api/sellers`, {
      headers: { cookie: session }
    });
    expect(persisted.status).toBe(200);
    const view = await persisted.json() as { sellers: Array<{ slug: string; role: string }> };
    expect(view.sellers).toHaveLength(1);
    expect(view.sellers[0]).toMatchObject({ slug, role: "seller_owner" });
  } finally {
    await Promise.all([stopBff(first), stopBff(second)]);
    await redis.stop();
    await Promise.all([
      rm(join(process.cwd(), firstDist), { recursive: true, force: true }),
      rm(join(process.cwd(), secondDist), { recursive: true, force: true })
    ]);
    await Promise.all(generatedFiles.map((file, index) => writeFile(join(process.cwd(), file), originals[index] ?? "")));
  }
}, 40_000);

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
      // The independent BFF is still starting.
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
