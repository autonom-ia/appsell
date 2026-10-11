import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtemp, rm } from "node:fs/promises";
import { createConnection, createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";

import Redis from "ioredis";

export interface IsolatedRedis {
  url: string;
  port: number;
  pid: number;
  client: Redis;
  stopServer: () => Promise<void>;
  restartServer: () => Promise<void>;
  stop: () => Promise<void>;
}

export async function startIsolatedRedis(): Promise<IsolatedRedis> {
  const port = await unusedNonDefaultPort();
  const directory = await mkdtemp(join(tmpdir(), "appsell-redis-test-"));
  const password = randomBytes(32).toString("base64url");
  const url = `redis://:${encodeURIComponent(password)}@127.0.0.1:${port}/0`;
  let process = spawnRedis(port, directory, password);
  let client = createProbeClient(url);
  try {
    await waitUntilReady(process, port);
    await client.connect();
    if (await client.ping() !== "PONG") throw new Error("Isolated Redis ping failed.");
    await assertOwnedRedis(client, process);
  } catch (error) {
    await stopProcess(process);
    await rm(directory, { recursive: true, force: true });
    throw error;
  }
  const isolated: IsolatedRedis = {
    url,
    port,
    get pid() { return process.pid!; },
    get client() { return client; },
    stopServer: async () => {
      client.disconnect();
      await stopProcess(process);
    },
    restartServer: async () => {
      if (process.exitCode === null) throw new Error("Isolated redis-server is still running.");
      process = spawnRedis(port, directory, password);
      client = createProbeClient(url);
      try {
        await waitUntilReady(process, port);
        await client.connect();
        if (await client.ping() !== "PONG") throw new Error("Restarted isolated Redis ping failed.");
        await assertOwnedRedis(client, process);
      } catch (error) {
        client.disconnect();
        await stopProcess(process);
        throw error;
      }
    },
    stop: async () => {
      try {
        if (client.status === "ready") await client.quit();
        else client.disconnect();
      } finally {
        await stopProcess(process);
        await rm(directory, { recursive: true, force: true });
      }
    }
  };
  return isolated;
}

function spawnRedis(port: number, directory: string, password: string) {
  return spawn(redisBinary(), [
    "--bind", "127.0.0.1",
    "--port", String(port),
    "--dir", directory,
    "--save", "",
    "--appendonly", "no",
    "--protected-mode", "yes",
    "--requirepass", password
  ], { stdio: "ignore" });
}

function createProbeClient(url: string) {
  return new Redis(url, { lazyConnect: true, maxRetriesPerRequest: 1, retryStrategy: () => null });
}

export async function unusedNonDefaultPort() {
  while (true) {
    const port = await new Promise<number>((resolve, reject) => {
      const server = createServer();
      server.once("error", reject);
      server.listen(0, "127.0.0.1", () => {
        const address = server.address();
        if (!address || typeof address === "string") return reject(new Error("Could not allocate test port."));
        const selected = address.port;
        server.close((error) => error ? reject(error) : resolve(selected));
      });
    });
    if (port !== 6379) return port;
  }
}

function redisBinary() {
  return process.env.REDIS_SERVER_BIN || "redis-server";
}

async function waitUntilReady(process: ChildProcess, port: number) {
  const deadline = Date.now() + 5_000;
  while (Date.now() < deadline) {
    if (process.exitCode !== null) throw new Error(`Isolated redis-server exited with ${process.exitCode}.`);
    if (await canConnect(port)) return;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error("Isolated redis-server did not become ready.");
}

function canConnect(port: number) {
  return new Promise<boolean>((resolve) => {
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

async function stopProcess(process: ChildProcess) {
  if (process.exitCode !== null) return;
  const exited = new Promise<void>((resolve) => process.once("exit", () => resolve()));
  process.kill("SIGTERM");
  await Promise.race([exited, new Promise<void>((resolve) => setTimeout(resolve, 2_000))]);
  if (process.exitCode === null) {
    process.kill("SIGKILL");
    await exited;
  }
}

async function assertOwnedRedis(client: Redis, process: ChildProcess) {
  const expectedPid = process.pid;
  const match = (await client.info("server")).match(/^process_id:(\d+)$/m);
  if (!expectedPid || Number(match?.[1]) !== expectedPid) {
    throw new Error("Refusing isolated Redis test: connected process is not the spawned child.");
  }
}
