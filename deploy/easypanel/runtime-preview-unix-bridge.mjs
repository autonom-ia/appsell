import { chmod, lstat, unlink } from "node:fs/promises";
import net from "node:net";
import { pathToFileURL } from "node:url";

export const DEFAULT_BRIDGE_CONFIG = Object.freeze({
  socketPath: "/transport/sell.sock",
  upstreamHost: "85.31.60.100",
  upstreamPort: 443,
});

export async function startUnixBridge(config = DEFAULT_BRIDGE_CONFIG) {
  const { socketPath, upstreamHost, upstreamPort } = config;
  if (typeof socketPath !== "string" || !socketPath.startsWith("/")) {
    throw new TypeError("runtime-preview bridge socket path is invalid");
  }
  if (typeof upstreamHost !== "string" || upstreamHost.length === 0) {
    throw new TypeError("runtime-preview bridge upstream host is invalid");
  }
  if (!Number.isInteger(upstreamPort) || upstreamPort < 1 || upstreamPort > 65535) {
    throw new TypeError("runtime-preview bridge upstream port is invalid");
  }
  try {
    await lstat(socketPath);
    throw new Error("runtime-preview bridge socket already exists");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }

  const sockets = new Set();
  let closed = false;
  const server = net.createServer((client) => {
    sockets.add(client);
    client.once("close", () => sockets.delete(client));
    const upstream = net.createConnection({ host: upstreamHost, port: upstreamPort });
    sockets.add(upstream);
    upstream.once("close", () => sockets.delete(upstream));
    client.once("error", () => upstream.destroy());
    upstream.once("error", () => client.destroy());
    client.once("close", () => upstream.destroy());
    upstream.once("close", () => client.destroy());
    client.pipe(upstream);
    upstream.pipe(client);
  });

  async function removeOwnedSocket() {
    try {
      const socket = await lstat(socketPath);
      if (!socket.isSocket()) throw new Error("runtime-preview bridge path changed type");
      await unlink(socketPath);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }

  async function close() {
    if (closed) return;
    closed = true;
    for (const socket of sockets) socket.destroy();
    if (server.listening) {
      await new Promise((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      });
    }
    await removeOwnedSocket();
  }

  try {
    await new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen(socketPath, () => {
        server.removeListener("error", reject);
        server.on("error", (error) => {
          console.error(`runtime-preview bridge error: ${error.message}`);
        });
        resolve();
      });
    });
    await chmod(socketPath, 0o600);
    const socket = await lstat(socketPath);
    if (!socket.isSocket() || (socket.mode & 0o777) !== 0o600) {
      throw new Error("runtime-preview bridge socket permissions are invalid");
    }
  } catch (error) {
    await close();
    throw error;
  }

  return { close };
}

async function runCli() {
  const expectedSocket = process.env.E2E_RUNTIME_BRIDGE_SOCKET;
  if (expectedSocket !== DEFAULT_BRIDGE_CONFIG.socketPath) {
    throw new Error("runtime-preview bridge socket environment drifted");
  }
  const bridge = await startUnixBridge();
  console.log(
    `runtime-preview Unix bridge ready for ${DEFAULT_BRIDGE_CONFIG.upstreamHost}:${DEFAULT_BRIDGE_CONFIG.upstreamPort}`,
  );
  let stopping = false;
  async function stop(exitCode) {
    if (stopping) return;
    stopping = true;
    try {
      await bridge.close();
      process.exit(exitCode);
    } catch (error) {
      console.error(`runtime-preview bridge shutdown failed: ${error.message}`);
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
    console.error(`runtime-preview bridge failed: ${error.message}`);
    process.exitCode = 1;
  }
}
