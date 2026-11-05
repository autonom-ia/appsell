import assert from "node:assert/strict";
import { lstat, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  bridgeConfigFromEnv,
  DEFAULT_BRIDGE_CONFIG,
  startUnixBridge,
  UPSTREAM_HOST_ENV,
} from "./runtime-preview-unix-bridge.mjs";

function listenTcp(server) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen({ host: "127.0.0.1", port: 0 }, () => {
      server.removeListener("error", reject);
      resolve(server.address());
    });
  });
}

function closeServer(server) {
  return new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
}

function exchange(socketPath, payload) {
  return new Promise((resolve, reject) => {
    const client = net.createConnection({ path: socketPath });
    const timer = setTimeout(() => {
      client.destroy();
      reject(new Error("bridge exchange timed out"));
    }, 5_000);
    client.once("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    client.once("data", (data) => {
      clearTimeout(timer);
      client.destroy();
      resolve(data.toString("utf8"));
    });
    client.once("connect", () => client.write(payload));
  });
}

test("production bridge defaults are immutable and carry no upstream host", () => {
  assert.deepEqual(DEFAULT_BRIDGE_CONFIG, {
    socketPath: "/transport/sell.sock",
    upstreamPort: 443,
  });
  assert.equal(Object.isFrozen(DEFAULT_BRIDGE_CONFIG), true);
  assert.equal(UPSTREAM_HOST_ENV, "APPSELL_RUNTIME_PREVIEW_PUBLIC_IPV4");
});

test("bridge upstream comes only from the required IPv4 environment variable", () => {
  const config = bridgeConfigFromEnv({ [UPSTREAM_HOST_ENV]: "192.0.2.10" });
  assert.deepEqual(config, {
    socketPath: "/transport/sell.sock",
    upstreamPort: 443,
    upstreamHost: "192.0.2.10",
  });
  assert.equal(Object.isFrozen(config), true);
  for (const env of [{}, { [UPSTREAM_HOST_ENV]: "" }, { [UPSTREAM_HOST_ENV]: "sell.autonomia.site" }]) {
    assert.throws(() => bridgeConfigFromEnv(env), /APPSELL_RUNTIME_PREVIEW_PUBLIC_IPV4 is required/);
  }
});

test("Unix bridge owns a mode-600 socket and forwards only to its configured TCP peer", async () => {
  const tempDirectory = await mkdtemp(path.join(os.tmpdir(), "appsell-unix-bridge-"));
  const socketPath = path.join(tempDirectory, "sell.sock");
  const upstream = net.createServer((socket) => {
    socket.once("data", (data) => socket.write(`sell-web:${data.toString("utf8")}`));
  });
  const upstreamAddress = await listenTcp(upstream);
  let bridge;

  try {
    bridge = await startUnixBridge({
      socketPath,
      upstreamHost: "127.0.0.1",
      upstreamPort: upstreamAddress.port,
    });
    const socket = await lstat(socketPath);
    assert.equal(socket.isSocket(), true);
    assert.equal(socket.mode & 0o777, 0o600);
    assert.equal(await exchange(socketPath, "probe"), "sell-web:probe");
  } finally {
    if (bridge) {
      await bridge.close();
      await assert.rejects(lstat(socketPath), { code: "ENOENT" });
    }
    if (upstream.listening) await closeServer(upstream);
    await rm(tempDirectory, { recursive: true });
  }
});

test("Unix bridge refuses and preserves a pre-existing transport path", async () => {
  const tempDirectory = await mkdtemp(path.join(os.tmpdir(), "appsell-unix-bridge-path-"));
  const socketPath = path.join(tempDirectory, "sell.sock");
  await writeFile(socketPath, "sentinel", { mode: 0o600 });
  try {
    await assert.rejects(
      startUnixBridge({ socketPath, upstreamHost: "127.0.0.1", upstreamPort: 9 }),
      /socket already exists/,
    );
    assert.equal(await readFile(socketPath, "utf8"), "sentinel");
  } finally {
    await rm(tempDirectory, { recursive: true });
  }
});
