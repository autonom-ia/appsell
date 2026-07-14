import { spawn, type ChildProcess } from "node:child_process";
import { createHash, createHmac, randomBytes, randomUUID } from "node:crypto";
import { readFile, rm, writeFile } from "node:fs/promises";
import { createConnection } from "node:net";
import { join } from "node:path";

import { expect, test } from "vitest";

import { unusedNonDefaultPort } from "./helpers/isolated-redis";

const domainApiUrl = process.env.APPSELL_TEST_DOMAIN_API_URL;
const endpointKey = "endpoint_0123456789abcdefghijklmnopqrstuv";
const missingSecretEndpointKey = "endpoint_other_0123456789abcdefghijklmnop";

test.skipIf(!domainApiUrl)("signed webhook crosses the real BFF, API, and PostgreSQL", async () => {
  const generatedFiles = ["next-env.d.ts", "tsconfig.json"];
  const originals = await Promise.all(generatedFiles.map((file) => readFile(join(process.cwd(), file))));
  const firstPort = await unusedNonDefaultPort();
  const secondPort = await unusedNonDefaultPort();
  const unavailableRedisPort = await unusedNonDefaultPort();
  const suffix = `${process.pid}-${Date.now()}`;
  const firstDist = `.next-webhook-${suffix}-one`;
  const secondDist = `.next-webhook-${suffix}-two`;
  const secret = Buffer.from(requiredEnvironment("APPSELL_TEST_WEBHOOK_SECRET_B64"), "base64");
  const shared = {
    APPSELL_DOMAIN_API_URL: requiredEnvironment("APPSELL_TEST_DOMAIN_API_URL"),
    APPSELL_INTERNAL_ISSUER: requiredEnvironment("APPSELL_INTERNAL_ISSUER"),
    APPSELL_INTERNAL_AUDIENCE: requiredEnvironment("APPSELL_INTERNAL_AUDIENCE"),
    APPSELL_IDENTITY_ISSUER: requiredEnvironment("APPSELL_IDENTITY_ISSUER"),
    APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON: requiredEnvironment("APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON"),
    APPSELL_SERVICE_CREDENTIAL_B64: requiredEnvironment("APPSELL_SERVICE_CREDENTIAL_B64"),
    APPSELL_SESSION_HMAC_KEY_B64: requiredEnvironment("APPSELL_TEST_WEBHOOK_SESSION_KEY_B64"),
    APPSELL_CSRF_HMAC_KEY_B64: requiredEnvironment("APPSELL_TEST_WEBHOOK_CSRF_KEY_B64"),
    APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: JSON.stringify([{
      id: "webhook-test-v1",
      key: requiredEnvironment("APPSELL_TEST_WEBHOOK_VAULT_KEY_B64"),
      active: true
    }]),
    APPSELL_REDIS_URL: `redis://127.0.0.1:${unavailableRedisPort}`,
    APPSELL_PUBLIC_ORIGIN: `http://127.0.0.1:${firstPort}`,
    NEXT_TELEMETRY_DISABLED: "1"
  };
  const first = startBff(firstPort, firstDist, shared);
  let second: ChildProcess | undefined;

  try {
    await waitForBff(first, firstPort);
    second = startBff(secondPort, secondDist, shared);
    await waitForBff(second, secondPort);
    await expectPortUnavailable(unavailableRedisPort);
    const eventId = `evt-e2e-${randomUUID()}`;
    const timestamp = String(Math.floor(Date.now() / 1_000));
    const body = Buffer.from(
      `{\n  "occurred_at": "${new Date().toISOString()}",\n` +
      `  "external_purchase_id": "compra-${randomUUID()}",\n` +
      '  "external_product_id": "códex-na-prática",\n' +
      '  "event_type": "purchase.approved"\n}\n',
      "utf8"
    );

    const accepted = await sendWebhook(firstPort, { eventId, timestamp, body, secret });
    expect(accepted.status, await accepted.clone().text()).toBe(202);
    expect(await accepted.json()).toEqual({ status: "accepted" });
    expect(await databaseEventDigest(eventId)).toBe(
      `${createHash("sha256").update(body).digest("hex")}|${body.length}`
    );

    await restartApi(requiredEnvironment("APPSELL_TEST_COMPOSE_PROJECT"));
    const replayed = await sendWebhook(secondPort, { eventId, timestamp, body, secret });
    expect(replayed.status, await replayed.clone().text()).toBe(202);
    expect(await replayed.json()).toEqual({ status: "replayed" });

    const stateBeforeConflict = await databaseState();
    const divergentBody = Buffer.from(body.toString("utf8").replace("purchase.approved", "purchase.refunded"));
    const divergent = await sendWebhook(firstPort, { eventId, timestamp, body: divergentBody, secret });
    expect(divergent.status).toBe(409);
    expect(await divergent.json()).toEqual({ error: "webhook_event_conflict" });
    const stateAfterConflict = await databaseState();
    expectReplayConsumedWithoutDomainMutation(stateBeforeConflict, stateAfterConflict);

    let stateBeforeRejected = stateAfterConflict;
    const invalidSignature = await sendWebhook(secondPort, {
      eventId: `evt-invalid-${randomUUID()}`,
      timestamp,
      body,
      secret: randomBytes(32)
    });
    expect(invalidSignature.status).toBe(401);
    expect(await invalidSignature.json()).toEqual({ error: "webhook_rejected" });
    let stateAfterRejected = await databaseState();
    expectReplayConsumedWithoutDomainMutation(stateBeforeRejected, stateAfterRejected);
    stateBeforeRejected = stateAfterRejected;

    const unavailable = await sendWebhook(secondPort, {
      endpointKey: missingSecretEndpointKey,
      eventId: `evt-unavailable-${randomUUID()}`,
      timestamp,
      body,
      secret: randomBytes(32)
    });
    expect(unavailable.status).toBe(503);
    expect(await unavailable.json()).toEqual({ error: "webhook_unavailable" });
    stateAfterRejected = await databaseState();
    expectReplayConsumedWithoutDomainMutation(stateBeforeRejected, stateAfterRejected);
    stateBeforeRejected = stateAfterRejected;

    const tenantInjectedBody = Buffer.from(body.toString("utf8").replace(
      /}\n$/, ',"seller_organization_id":"00000000-0000-0000-0000-000000000000"}\n'
    ));
    expect(tenantInjectedBody.equals(body)).toBe(false);
    const tenantInjected = await sendWebhook(firstPort, {
      eventId: `evt-tenant-${randomUUID()}`,
      timestamp,
      body: tenantInjectedBody,
      secret
    });
    expect(tenantInjected.status).toBe(401);
    stateAfterRejected = await databaseState();
    expectReplayConsumedWithoutDomainMutation(stateBeforeRejected, stateAfterRejected);
    stateBeforeRejected = stateAfterRejected;

    const staleTimestamp = String(Number(timestamp) - 901);
    const stale = await sendWebhook(firstPort, {
      eventId: `evt-stale-${randomUUID()}`,
      timestamp: staleTimestamp,
      body,
      secret
    });
    expect(stale.status).toBe(401);
    stateAfterRejected = await databaseState();
    expectReplayConsumedWithoutDomainMutation(stateBeforeRejected, stateAfterRejected);

    const exactEventId = `evt-exact-${randomUUID()}`;
    const exactBase = Buffer.from(JSON.stringify({
      event_type: "purchase.approved",
      external_product_id: "codex-na-pratica",
      external_purchase_id: `purchase-${randomUUID()}`,
      occurred_at: new Date().toISOString()
    }));
    const exactLimit = Buffer.concat([exactBase, Buffer.alloc(64 * 1024 - exactBase.length, 0x20)]);
    const exact = await sendWebhook(secondPort, {
      eventId: exactEventId,
      timestamp,
      body: exactLimit,
      secret
    });
    expect(exact.status, await exact.clone().text()).toBe(202);
    expect(await databaseEventDigest(exactEventId)).toBe(
      `${createHash("sha256").update(exactLimit).digest("hex")}|65536`
    );

    const stateBeforeOversized = await databaseState();
    const oversized = Buffer.concat([exactLimit, Buffer.from(" ")]);
    const tooLarge = await sendWebhook(secondPort, {
      eventId: `evt-large-${randomUUID()}`,
      timestamp,
      body: oversized,
      secret
    });
    expect(tooLarge.status).toBe(413);
    expect(await tooLarge.json()).toEqual({ error: "payload_too_large" });
    expect(await databaseState()).toEqual(stateBeforeOversized);
  } finally {
    await Promise.all([stopBff(first), ...(second ? [stopBff(second)] : [])]);
    await Promise.all([
      rm(join(process.cwd(), firstDist), { recursive: true, force: true }),
      rm(join(process.cwd(), secondDist), { recursive: true, force: true })
    ]);
    await Promise.all(generatedFiles.map((file, index) => writeFile(
      join(process.cwd(), file), originals[index] ?? ""
    )));
  }
}, 60_000);

async function sendWebhook(
  port: number,
  input: { endpointKey?: string; eventId: string; timestamp: string; body: Buffer; secret: Buffer }
) {
  const requestEndpointKey = input.endpointKey ?? endpointKey;
  const payloadSha256 = createHash("sha256").update(input.body).digest("hex");
  const canonical = [
    "appsell.webhook.generic-hmac.v1",
    requestEndpointKey,
    input.timestamp,
    input.eventId,
    payloadSha256
  ].join("\n");
  const signature = createHmac("sha256", input.secret).update(canonical).digest("hex");
  return fetch(`http://127.0.0.1:${port}/api/webhooks/checkouts/${requestEndpointKey}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-appsell-webhook-event-id": input.eventId,
      "x-appsell-webhook-timestamp": input.timestamp,
      "x-appsell-webhook-signature": `v1=${signature}`
    },
    body: new Uint8Array(input.body)
  });
}

async function restartApi(project: string) {
  const child = spawn("docker", ["compose", "-p", project, "restart", "api"], {
    cwd: process.cwd(),
    env: process.env,
    stdio: ["ignore", "pipe", "pipe"]
  });
  let diagnostics = "";
  child.stdout?.on("data", (chunk) => { diagnostics = `${diagnostics}${String(chunk)}`.slice(-3_000); });
  child.stderr?.on("data", (chunk) => { diagnostics = `${diagnostics}${String(chunk)}`.slice(-3_000); });
  const exitCode = await new Promise<number | null>((resolve) => child.once("exit", resolve));
  if (exitCode !== 0) throw new Error(`API restart failed: ${diagnostics}`);
  const deadline = Date.now() + 20_000;
  const healthUrl = new URL("/health/ready", requiredEnvironment("APPSELL_TEST_DOMAIN_API_URL"));
  while (Date.now() < deadline) {
    try {
      if ((await fetch(healthUrl)).ok) return;
    } catch {
      // The API container is still restarting.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`API did not recover after restart: ${diagnostics}`);
}

async function databaseState() {
  const project = requiredEnvironment("APPSELL_TEST_COMPOSE_PROJECT");
  const query = [
    "select count(*) from app_private.bff_request_replay",
    "select count(*) from webhook_event",
    "select count(*) from app_private.outbox_event",
    "select count(*) from audit_event where action='commerce.webhook_received'"
  ].join(";");
  const child = spawn(
    "docker",
    ["compose", "-p", project, "exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "appsell", "-Atc", query],
    { cwd: process.cwd(), env: process.env, stdio: ["ignore", "pipe", "pipe"] }
  );
  let stdout = "";
  let stderr = "";
  child.stdout?.on("data", (chunk) => { stdout += String(chunk); });
  child.stderr?.on("data", (chunk) => { stderr += String(chunk); });
  const exitCode = await new Promise<number | null>((resolve) => child.once("exit", resolve));
  if (exitCode !== 0) throw new Error(`PostgreSQL state probe failed: ${stderr.slice(-1_000)}`);
  const values = stdout.trim().split("\n").map(Number);
  if (values.length !== 4 || values.some((value) => !Number.isSafeInteger(value))) {
    throw new Error(`PostgreSQL state probe returned invalid counts: ${stdout.slice(-1_000)}`);
  }
  return {
    replayRequests: values[0]!,
    webhookEvents: values[1]!,
    outboxEvents: values[2]!,
    webhookAudits: values[3]!
  };
}

function expectReplayConsumedWithoutDomainMutation(
  before: Awaited<ReturnType<typeof databaseState>>,
  after: Awaited<ReturnType<typeof databaseState>>
) {
  expect(after).toEqual({
    ...before,
    replayRequests: before.replayRequests + 1
  });
}

async function databaseEventDigest(eventId: string) {
  const eventHash = createHash("sha256").update(eventId).digest("hex");
  return databaseScalar(
    "select payload_sha256||'|'||payload_octets from webhook_event " +
      `where external_event_id_sha256='${eventHash}'`
  );
}

async function databaseScalar(query: string) {
  const project = requiredEnvironment("APPSELL_TEST_COMPOSE_PROJECT");
  const child = spawn(
    "docker",
    ["compose", "-p", project, "exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "appsell", "-Atc", query],
    { cwd: process.cwd(), env: process.env, stdio: ["ignore", "pipe", "pipe"] }
  );
  let stdout = "";
  let stderr = "";
  child.stdout?.on("data", (chunk) => { stdout += String(chunk); });
  child.stderr?.on("data", (chunk) => { stderr += String(chunk); });
  const exitCode = await new Promise<number | null>((resolve) => child.once("exit", resolve));
  if (exitCode !== 0) throw new Error(`PostgreSQL scalar probe failed: ${stderr.slice(-1_000)}`);
  return stdout.trim();
}

async function expectPortUnavailable(port: number) {
  await new Promise<void>((resolve, reject) => {
    const socket = createConnection({ host: "127.0.0.1", port });
    const timer = setTimeout(() => {
      socket.destroy();
      reject(new Error(`Redis probe timed out on port ${port}`));
    }, 1_000);
    socket.once("connect", () => {
      clearTimeout(timer);
      socket.destroy();
      reject(new Error(`Redis unexpectedly accepted a connection on port ${port}`));
    });
    socket.once("error", () => {
      clearTimeout(timer);
      resolve();
    });
  });
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
  const deadline = Date.now() + 20_000;
  let diagnostics = "";
  process.stderr?.on("data", (chunk) => { diagnostics = `${diagnostics}${String(chunk)}`.slice(-3_000); });
  while (Date.now() < deadline) {
    if (process.exitCode !== null) throw new Error(`BFF ${port} exited: ${diagnostics}`);
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/webhooks/checkouts/invalid`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}"
      });
      if (response.status === 400) return;
    } catch {
      // The independent BFF is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`BFF ${port} did not start: ${diagnostics}`);
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
