import net from "node:net";
import { pathToFileURL } from "node:url";

export const DEFAULT_PROXY_CONFIG = Object.freeze({
  listenHosts: Object.freeze(["127.0.0.1", "::1"]),
  listenPort: 443,
  upstreamSocket: "/transport/sell.sock",
});

export async function startLoopbackProxy(config = DEFAULT_PROXY_CONFIG) {
  const { listenHosts, listenPort, upstreamSocket } = config;
  if (!Array.isArray(listenHosts) || listenHosts.length === 0) {
    throw new TypeError("runtime-preview proxy requires at least one listen host");
  }
  if (!Number.isInteger(listenPort) || listenPort < 0 || listenPort > 65535) {
    throw new TypeError("runtime-preview proxy listen port is invalid");
  }
  if (typeof upstreamSocket !== "string" || !upstreamSocket.startsWith("/")) {
    throw new TypeError("runtime-preview proxy upstream socket is invalid");
  }

  const sockets = new Set();
  const servers = [];
  let closed = false;

  function track(socket) {
    sockets.add(socket);
    socket.once("close", () => sockets.delete(socket));
    return socket;
  }

  function proxyConnection(client) {
    track(client);
    const upstream = track(net.createConnection({ path: upstreamSocket }));
    client.once("error", () => upstream.destroy());
    upstream.once("error", () => client.destroy());
    client.once("close", () => upstream.destroy());
    upstream.once("close", () => client.destroy());
    client.pipe(upstream);
    upstream.pipe(client);
  }

  function listen(host) {
    return new Promise((resolve, reject) => {
      const server = net.createServer(proxyConnection);
      server.once("error", reject);
      server.listen({ host, port: listenPort, ipv6Only: host === "::1" }, () => {
        server.removeListener("error", reject);
        server.on("error", (error) => {
          console.error(`runtime-preview proxy error on ${host}: ${error.message}`);
        });
        resolve(server);
      });
    });
  }

  async function close() {
    if (closed) return;
    closed = true;
    for (const socket of sockets) socket.destroy();
    await Promise.all(
      servers.map(
        (server) =>
          new Promise((resolve, reject) => {
            server.close((error) => (error ? reject(error) : resolve()));
          }),
      ),
    );
  }

  try {
    for (const host of listenHosts) servers.push(await listen(host));
  } catch (error) {
    await close();
    throw error;
  }

  return {
    addresses: servers.map((server) => server.address()),
    close,
  };
}

async function runCli() {
  const expectedSocket = process.env.E2E_RUNTIME_BRIDGE_SOCKET;
  if (expectedSocket !== DEFAULT_PROXY_CONFIG.upstreamSocket) {
    throw new Error("runtime-preview proxy socket environment drifted");
  }
  const proxy = await startLoopbackProxy();
  console.log(
    `runtime-preview loopback proxy ready for ${DEFAULT_PROXY_CONFIG.upstreamSocket}`,
  );
  let stopping = false;
  async function stop(exitCode) {
    if (stopping) return;
    stopping = true;
    try {
      await proxy.close();
      process.exit(exitCode);
    } catch (error) {
      console.error(`runtime-preview proxy shutdown failed: ${error.message}`);
      process.exit(1);
    }
  }
  process.once("SIGINT", () => void stop(130));
  process.once("SIGTERM", () => void stop(0));
}

const invokedUrl = process.argv[1] ? pathToFileURL(process.argv[1]).href : "";
if (import.meta.url === invokedUrl) {
  try {
    await runCli();
  } catch (error) {
    console.error(`runtime-preview proxy failed: ${error.message}`);
    process.exitCode = 1;
  }
}
