import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtemp, rm } from "node:fs/promises";
import { createConnection, createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";

import Redis from "ioredis";

const command = process.argv[2];
const args = process.argv.slice(3);
if (!command) throw new Error("Usage: with-isolated-redis.mjs <command> [...args]");

const port = await unusedPort();
const directory = await mkdtemp(join(tmpdir(), "appsell-e2e-redis-"));
const password = randomBytes(32).toString("base64url");
const binary = process.env.REDIS_SERVER_BIN || "redis-server";
const redis = spawn(binary, [
  "--bind", "127.0.0.1", "--port", String(port), "--dir", directory,
  "--save", "", "--appendonly", "no", "--protected-mode", "yes",
  "--requirepass", password
], { stdio: "ignore" });
const url = `redis://:${encodeURIComponent(password)}@127.0.0.1:${port}/0`;
const probe = new Redis(url, { lazyConnect: true, maxRetriesPerRequest: 1, retryStrategy: () => null });
let child;

try {
  await waitForRedis(redis, port);
  await probe.connect();
  if (await probe.ping() !== "PONG") throw new Error("isolated Redis ping failed");
  const redisPid = Number((await probe.info("server")).match(/^process_id:(\d+)$/m)?.[1]);
  if (!redis.pid || redisPid !== redis.pid) throw new Error("connected Redis is not the spawned child");
  child = spawn(command, args, {
    stdio: "inherit",
    env: {
      ...process.env,
      APPSELL_REDIS_URL: url,
      APPSELL_REDIS_PREFIX: `appsell:e2e:run-${process.pid}-${Date.now()}`,
      APPSELL_SESSION_HMAC_KEY_B64: randomBytes(32).toString("base64")
    }
  });
  const exitCode = await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => resolve(code ?? (signal ? 1 : 0)));
  });
  process.exitCode = exitCode;
} finally {
  probe.disconnect();
  if (child?.exitCode === null) child.kill("SIGTERM");
  await stopProcess(redis);
  await rm(directory, { recursive: true, force: true });
}

async function unusedPort() {
  while (true) {
    const port = await new Promise((resolve, reject) => {
      const server = createServer();
      server.once("error", reject);
      server.listen(0, "127.0.0.1", () => {
        const address = server.address();
        if (!address || typeof address === "string") return reject(new Error("Could not allocate port."));
        server.close((error) => error ? reject(error) : resolve(address.port));
      });
    });
    if (port !== 6379) return port;
  }
}

async function waitForRedis(process, port) {
  const deadline = Date.now() + 5_000;
  while (Date.now() < deadline) {
    if (process.exitCode !== null) throw new Error(`redis-server exited with ${process.exitCode}`);
    if (await canConnect(port)) return;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error("isolated redis-server did not become ready");
}

function canConnect(port) {
  return new Promise((resolve) => {
    const socket = createConnection({ host: "127.0.0.1", port });
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("error", () => resolve(false));
    socket.setTimeout(100, () => {
      socket.destroy();
      resolve(false);
    });
  });
}

async function stopProcess(process) {
  if (process.exitCode !== null) return;
  const exited = new Promise((resolve) => process.once("exit", resolve));
  process.kill("SIGTERM");
  await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 2_000))]);
  if (process.exitCode === null) {
    process.kill("SIGKILL");
    await exited;
  }
}
