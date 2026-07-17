import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import { readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { expect, test } from "vitest";

import { startIsolatedRedis, unusedNonDefaultPort } from "./helpers/isolated-redis";

test("two real Next BFF processes share an opaque Redis session", async () => {
  const generatedFiles = ["next-env.d.ts", "tsconfig.json"];
  const originals = await Promise.all(generatedFiles.map((file) => readFile(join(process.cwd(), file))));
  const redis = await startIsolatedRedis();
  const firstPort = await unusedNonDefaultPort();
  const secondPort = await unusedNonDefaultPort();
  const suffix = `${process.pid}-${Date.now()}`;
  const firstDist = `.next-bff-${suffix}-one`;
  const secondDist = `.next-bff-${suffix}-two`;
  const vaultKey = randomBytes(32);
  const shared = {
    APPSELL_REDIS_URL: redis.url,
    APPSELL_REDIS_PREFIX: `appsell:test:bff-${suffix}`,
    APPSELL_SESSION_HMAC_KEY_B64: randomBytes(32).toString("base64"),
    APPSELL_CSRF_HMAC_KEY_B64: randomBytes(32).toString("base64"),
    APPSELL_PUBLIC_ORIGIN: `http://localhost:${firstPort}`,
    APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: JSON.stringify([
      { id: "test-v1", key: vaultKey.toString("base64"), active: true }
    ]),
    NEXT_TELEMETRY_DISABLED: "1"
  };
  const first = startBff(firstPort, firstDist, shared);
  let second: ChildProcess | undefined;

  try {
    await waitForBff(first, firstPort);
    second = startBff(secondPort, secondDist, shared);
    await waitForBff(second, secondPort);
    expect(first.pid).toBeTypeOf("number");
    expect(second.pid).toBeTypeOf("number");
    expect(first.pid).not.toBe(second.pid);

    const login = await fetch(`http://127.0.0.1:${firstPort}/auth/login`, { redirect: "manual" });
    expect(login.status).toBe(307);
    const transactionCookie = cookiePair(login.headers.get("set-cookie"), "appsell_oauth_tx");
    const authorize = await fetch(requiredHeader(login, "location"), { redirect: "manual" });
    expect(authorize.status).toBe(307);

    const callbackFromFirst = new URL(requiredHeader(authorize, "location"));
    callbackFromFirst.port = String(secondPort);
    const callback = await fetch(callbackFromFirst, {
      redirect: "manual",
      headers: { cookie: transactionCookie }
    });
    expect(callback.status).toBe(307);
    const setCookie = callback.headers.get("set-cookie");
    const sessionCookie = cookiePair(setCookie, "appsell_session");
    const csrfCookie = cookiePair(setCookie, "appsell_csrf");
    const csrfToken = csrfCookie.slice(csrfCookie.indexOf("=") + 1);

    const sharedSession = await fetch(`http://127.0.0.1:${firstPort}/api/session`, {
      headers: { cookie: sessionCookie }
    });
    expect(sharedSession.status).toBe(200);
    const sessionView = await sharedSession.json() as {
      authenticated: boolean;
    };
    expect(sessionView.authenticated).toBe(true);
    expect(sessionView).not.toHaveProperty("csrf");

    await redis.client.shutdown("NOSAVE").catch(() => undefined);
    const unavailableLogout = await fetch(`http://localhost:${firstPort}/auth/logout`, {
      method: "POST",
      redirect: "manual",
      headers: {
        cookie: `${sessionCookie}; ${csrfCookie}`,
        origin: `http://localhost:${firstPort}`,
        "content-type": "application/x-www-form-urlencoded"
      },
      body: new URLSearchParams({ _csrf: csrfToken })
    });
    expect(unavailableLogout.status).toBe(503);
    expect(unavailableLogout.headers.get("set-cookie")).toBeNull();
  } finally {
    await Promise.all([stopBff(first), ...(second ? [stopBff(second)] : [])]);
    await redis.stop();
    await Promise.all([
      rm(join(process.cwd(), firstDist), { recursive: true, force: true }),
      rm(join(process.cwd(), secondDist), { recursive: true, force: true })
    ]);
    await Promise.all(generatedFiles.map((file, index) => writeFile(join(process.cwd(), file), originals[index] ?? "")));
  }
}, 30_000);

function startBff(port: number, distDir: string, shared: Record<string, string>) {
  return spawn(process.execPath, [join(process.cwd(), "node_modules/next/dist/bin/next"), "dev", "-H", "127.0.0.1", "-p", String(port)], {
    cwd: process.cwd(),
    env: { ...process.env, ...shared, APPSELL_NEXT_DIST_DIR: distDir },
    stdio: ["ignore", "pipe", "pipe"]
  });
}

async function waitForBff(process: ChildProcess, port: number) {
  const deadline = Date.now() + 15_000;
  let diagnostics = "";
  process.stderr?.on("data", (chunk) => { diagnostics = `${diagnostics}${String(chunk)}`.slice(-2_000); });
  while (Date.now() < deadline) {
    if (process.exitCode !== null) throw new Error(`BFF ${port} exited early: ${diagnostics}`);
    try {
      const response = await fetch(`http://127.0.0.1:${port}/`);
      if (response.ok) return;
    } catch {
      // The independent Next process is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`BFF ${port} did not become ready: ${diagnostics}`);
}

function requiredHeader(response: Response, name: string) {
  const value = response.headers.get(name);
  if (!value) throw new Error(`Missing ${name} response header.`);
  return value;
}

function cookiePair(setCookie: string | null, name: string) {
  const match = setCookie?.match(new RegExp(`(?:^|,\\s*)(${name}=[^;]+)`));
  if (!match) throw new Error(`Missing ${name} cookie.`);
  return match[1] ?? "";
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
