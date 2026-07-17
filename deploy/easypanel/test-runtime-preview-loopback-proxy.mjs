import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  DEFAULT_PROXY_CONFIG,
  startLoopbackProxy,
} from "./runtime-preview-loopback-proxy.mjs";

function closeServer(server) {
  return new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
}

function listenTcp(server, options) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(options, () => {
      server.removeListener("error", reject);
      resolve(server.address());
    });
  });
}

async function reservePort() {
  const server = net.createServer();
  const address = await listenTcp(server, { host: "127.0.0.1", port: 0 });
  await closeServer(server);
  return address.port;
}

function exchange(host, port, payload) {
  return new Promise((resolve, reject) => {
    const client = net.createConnection({ host, port });
    const timer = setTimeout(() => {
      client.destroy();
      reject(new Error("proxy exchange timed out"));
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

test("production defaults are immutable and limited to sell-web loopback forwarding", () => {
  assert.deepEqual(DEFAULT_PROXY_CONFIG, {
    listenHosts: ["127.0.0.1", "::1"],
    listenPort: 443,
    upstreamSocket: "/transport/sell.sock",
  });
  assert.equal(Object.isFrozen(DEFAULT_PROXY_CONFIG), true);
  assert.equal(Object.isFrozen(DEFAULT_PROXY_CONFIG.listenHosts), true);
});

test("proxy forwards IPv4 and IPv6 loopback bytes only to its configured Unix socket", async () => {
  const tempDirectory = await mkdtemp(path.join(os.tmpdir(), "appsell-loopback-proxy-"));
  const upstreamSocket = path.join(tempDirectory, "sell.sock");
  const upstream = net.createServer((socket) => {
    socket.once("data", (data) => socket.write(`upstream:${data.toString("utf8")}`));
  });
  let proxy;

  try {
    await listenTcp(upstream, upstreamSocket);
    const listenPort = await reservePort();
    proxy = await startLoopbackProxy({
      ...DEFAULT_PROXY_CONFIG,
      listenPort,
      upstreamSocket,
    });
    assert.deepEqual(
      proxy.addresses.map((address) => address.address),
      ["127.0.0.1", "::1"],
    );
    assert.equal(await exchange("127.0.0.1", listenPort, "ipv4"), "upstream:ipv4");
    assert.equal(await exchange("::1", listenPort, "ipv6"), "upstream:ipv6");
  } finally {
    if (proxy) await proxy.close();
    if (upstream.listening) await closeServer(upstream);
    await rm(tempDirectory, { recursive: true });
  }
});

test("a failed IPv6 bind releases the already-open IPv4 listener", async () => {
  const listenPort = await reservePort();
  const blocker = net.createServer();
  let probe;
  let unexpectedProxy;
  try {
    await listenTcp(blocker, { host: "::1", port: listenPort, ipv6Only: true });
    try {
      unexpectedProxy = await startLoopbackProxy({
        ...DEFAULT_PROXY_CONFIG,
        listenPort,
      });
      assert.fail("proxy accepted a failed second listener");
    } catch (error) {
      if (unexpectedProxy) throw error;
      assert.equal(error.code, "EADDRINUSE");
    }
    probe = net.createServer();
    await listenTcp(probe, { host: "127.0.0.1", port: listenPort });
  } finally {
    if (unexpectedProxy) await unexpectedProxy.close();
    if (probe?.listening) await closeServer(probe);
    if (blocker.listening) await closeServer(blocker);
  }
});
