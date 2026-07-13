import { execFile, spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { promisify } from "node:util";

import { describe, expect, it, vi } from "vitest";

import {
  accumulatePostgresWaitDiagnostics,
  emptyPostgresWaitDiagnostics,
  samplePostgresConnections
} from "../lib/capacity-postgres-sampler";

const execFileAsync = promisify(execFile);
const integrationContainer = process.env.APPSELL_TEST_POSTGRES_CONTAINER_ID;

describe("PostgreSQL capacity sampler", () => {
  it("separates the observer, runtime role and API application", async () => {
    const executor = vi.fn().mockResolvedValue({
      stdout: "24|3|11|10|9|8|6|4|8|0|1|0|1|1|1|1|1|1|1|7|Client/ClientRead:1,IO/WALWrite:7|IO/WALWrite:7|Lock/relation@api>autovacuum:2\n"
    });
    await expect(samplePostgresConnections("container", executor)).resolves.toEqual({
      maxConnections: 24,
      reservedConnections: 3,
      total: 11,
      workload: 10,
      runtime: 9,
      api: 8,
      active: 6,
      idle: 4,
      activeWaiting: 8,
      lockWaiting: 0,
      lwLockWaiting: 1,
      bufferPinWaiting: 0,
      clientWaiting: 1,
      ioWaiting: 1,
      ipcWaiting: 1,
      activityWaiting: 1,
      timeoutWaiting: 1,
      extensionWaiting: 1,
      otherWaiting: 1,
      apiActiveWaiting: 7,
      waitEvents: { ClientRead: 1, WALWrite: 7 },
      apiWaitEvents: { WALWrite: 7 },
      waitEventTypes: { "Client/ClientRead": 1, "IO/WALWrite": 7 },
      apiWaitEventTypes: { "IO/WALWrite": 7 },
      blockingEventScopes: { "Lock/relation@api>autovacuum": 2 }
    });
    expect(executor.mock.calls[0]?.[1]).toContain("PGAPPNAME=appsell_capacity_sampler");
    const query = String(executor.mock.calls[0]?.[1].at(-1));
    expect(query).toContain("activity as materialized");
    expect(query.match(/pg_stat_activity/g)).toHaveLength(1);
    expect(query).toContain(
      "select pid,backend_type,usename,application_name,state,wait_event_type,wait_event from pg_stat_activity"
    );
    expect(query).not.toMatch(/select \*/i);
    expect(query).not.toMatch(/\b(query|client_addr|backend_start|xact_start|query_start)\b/i);
    expect(query).toContain("pid<>pg_backend_pid()");
    expect(query).toContain("wait_event_type='Lock'");
    expect(query).toContain("wait_event_type='LWLock'");
    expect(query).toContain("wait_event_type='BufferPin'");
    expect(query).toContain("wait_event_type='Client'");
    expect(query).toContain("wait_event_type='IO'");
    expect(query).toContain("wait_event_type='IPC'");
    expect(query).toContain("group by wait_event_type,wait_event");
    expect(query).toContain("string_agg(wait_event_type || '/' || wait_event");
    expect(query).toContain("unnest(pg_blocking_pids(waiter.pid))");
    expect(query).toContain("lock_waiters as materialized");
    expect(query).toContain("from lock_waiters waiter cross join lateral");
    expect(query).toContain("left join activity blocker on blocker.pid=blocker_pid");
    expect(query).toContain("blocker.backend_type='autovacuum worker'");
    expect(query).toContain("wait_event_type || '/' || wait_event || '@' ||");
  });

  it("propagates docker/psql failure instead of returning a zero sample", async () => {
    const executor = vi.fn().mockRejectedValue(new Error("injected docker exec failure"));
    await expect(samplePostgresConnections("container", executor)).rejects.toThrow(
      "injected docker exec failure"
    );
  });

  it("rejects malformed samples", async () => {
    const executor = vi.fn().mockResolvedValue({ stdout: "24|3|not-a-number\n" });
    await expect(samplePostgresConnections("container", executor)).rejects.toThrow(
      "postgres_sample_invalid"
    );
  });

  it("rejects malformed exact wait-event diagnostics", async () => {
    const executor = vi.fn().mockResolvedValue({
      stdout: "24|3|11|10|9|8|6|4|8|0|1|0|1|1|1|1|1|1|1|7|IO/WALWrite:not-a-number|IO/WALWrite:7|api>other:1\n"
    });
    await expect(samplePostgresConnections("container", executor)).rejects.toThrow(
      "postgres_sample_invalid"
    );
  });

  it("rejects duplicate or non-opaque typed wait events", async () => {
    const duplicateExecutor = vi.fn().mockResolvedValue({
      stdout: "24|3|11|10|9|8|6|4|8|0|1|0|1|1|1|1|1|1|1|7|IO/WALWrite:1,IO/WALWrite:2|IO/WALWrite:1|api>other:1\n"
    });
    await expect(samplePostgresConnections("container", duplicateExecutor)).rejects.toThrow(
      "postgres_sample_invalid"
    );
    const identifierExecutor = vi.fn().mockResolvedValue({
      stdout: "24|3|11|10|9|8|6|4|8|0|1|0|1|1|1|1|1|1|1|7|IO/tenant-uuid:1|IO/WALWrite:1|api>other:1\n"
    });
    await expect(samplePostgresConnections("container", identifierExecutor)).rejects.toThrow(
      "postgres_sample_invalid"
    );
    const scopeExecutor = vi.fn().mockResolvedValue({
      stdout: "24|3|11|10|9|8|6|4|8|0|1|0|1|1|1|1|1|1|1|7|IO/WALWrite:1|IO/WALWrite:1|Lock/relation@api>tenant_001:1\n"
    });
    await expect(samplePostgresConnections("container", scopeExecutor)).rejects.toThrow(
      "postgres_sample_invalid"
    );
  });

  it("keeps identical event names separated by wait-event type", async () => {
    const executor = vi.fn().mockResolvedValue({
      stdout: "24|3|4|3|2|2|2|0|2|1|1|0|0|0|0|0|0|0|0|2|Lock/LockManager:1,LWLock/LockManager:1|Lock/LockManager:1,LWLock/LockManager:1|Lock/LockManager@api>unknown:1\n"
    });

    await expect(samplePostgresConnections("container", executor)).resolves.toMatchObject({
      waitEvents: { LockManager: 2 },
      apiWaitEvents: { LockManager: 2 },
      waitEventTypes: { "Lock/LockManager": 1, "LWLock/LockManager": 1 },
      blockingEventScopes: { "Lock/LockManager@api>unknown": 1 }
    });
  });

  it("force-stops a probe child that ignores graceful termination", async () => {
    const child = spawn(process.execPath, [
      "-e",
      "process.on('SIGTERM',()=>{});process.stdout.write('ready');setInterval(()=>{},1000)"
    ], { stdio: ["ignore", "pipe", "pipe"] });
    try {
      await once(child.stdout!, "data");
      await stopChild(child);
      expect(child.signalCode).toBe("SIGKILL");
    } finally {
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
      if (!(await exitedWithin(child, 1_000))) {
        throw new Error("stubborn_probe_emergency_cleanup_failed");
      }
    }
  });

  it.skipIf(!integrationContainer)(
    "correlates a real advisory lock waiter and blocker as api to api",
    async () => {
      const container = integrationContainer!;
      const lockKey = 746205401;
      const blocker = spawnPsql(container, `select pg_advisory_lock(${lockKey}); select pg_sleep(20)`);
      let waiter: ChildProcess | undefined;
      try {
        await waitForSqlCount(container,
          "select count(*) from pg_locks l join pg_stat_activity a using(pid) " +
          "where l.locktype='advisory' and l.granted " +
          "and a.application_name='appsell_capacity_api'", 1);
        waiter = spawnPsql(container, `select pg_advisory_lock(${lockKey})`);
        await waitForSqlCount(container,
          "select count(*) from pg_stat_activity where state='active' " +
          "and wait_event_type='Lock' and wait_event='advisory' " +
          "and application_name='appsell_capacity_api'", 1);

        const sample = await samplePostgresConnections(container);
        expect(sample.lockWaiting).toBeGreaterThanOrEqual(1);
        expect(sample.waitEventTypes["Lock/advisory"]).toBeGreaterThanOrEqual(1);
        expect(sample.apiWaitEventTypes["Lock/advisory"]).toBeGreaterThanOrEqual(1);
        expect(sample.blockingEventScopes["Lock/advisory@api>api"]).toBeGreaterThanOrEqual(1);
      } finally {
        await cleanupProbeSessions(container, [blocker, waiter]);
      }
    },
    15_000
  );

  it("attributes waits by phase and measures only gaps between consecutive full API-pool samples", () => {
    const sample = {
      activeWaiting: 10,
      apiActiveWaiting: 10,
      waitEvents: { ClientRead: 1, WALWrite: 9 },
      apiWaitEvents: { WALWrite: 9 },
      waitEventTypes: { "Client/ClientRead": 1, "IO/WALWrite": 9 },
      apiWaitEventTypes: { "IO/WALWrite": 9 },
      blockingEventScopes: { "Lock/relation@api>autovacuum": 2 }
    };
    const first = accumulatePostgresWaitDiagnostics(
      emptyPostgresWaitDiagnostics(), sample, "scenario_buyer_player", 900, 10
    );
    expect(first).toMatchObject({
      waitingSamplesByPhase: { scenario_buyer_player: 1 },
      fullApiPoolWaitingSamples: 1,
      fullApiPoolWaitingObservedMs: 0,
      maxConsecutiveFullApiPoolWaitingSamples: 1,
      maxConsecutiveFullApiPoolWaitingMs: 0,
      apiWaitEventPeaks: { WALWrite: 9 },
      waitEventTypePeaksByPhase: {
        scenario_buyer_player: { "Client/ClientRead": 1, "IO/WALWrite": 9 }
      },
      apiWaitEventTypePeaksByPhase: {
        scenario_buyer_player: { "IO/WALWrite": 9 }
      },
      blockingEventScopePeaksByPhase: {
        scenario_buyer_player: { "Lock/relation@api>autovacuum": 2 }
      }
    });
    const second = accumulatePostgresWaitDiagnostics(
      first, sample, "scenario_buyer_player", 250, 10
    );
    expect(second).toMatchObject({
      waitingSamplesByPhase: { scenario_buyer_player: 2 },
      fullApiPoolWaitingSamples: 2,
      fullApiPoolWaitingObservedMs: 250,
      maxConsecutiveFullApiPoolWaitingSamples: 2,
      maxConsecutiveFullApiPoolWaitingMs: 250
    });
    const reset = accumulatePostgresWaitDiagnostics(second, {
      activeWaiting: 1,
      apiActiveWaiting: 0,
      waitEvents: { ClientRead: 1 },
      apiWaitEvents: {},
      waitEventTypes: { "Client/ClientRead": 1 },
      apiWaitEventTypes: {},
      blockingEventScopes: {}
    }, "scenario_owner_catalog", 250, 10);
    expect(reset).toMatchObject({
      waitingSamplesByPhase: { scenario_buyer_player: 2, scenario_owner_catalog: 1 },
      currentConsecutiveFullApiPoolWaitingSamples: 0,
      currentConsecutiveFullApiPoolWaitingMs: 0,
      waitEventPeaks: { ClientRead: 1, WALWrite: 9 }
    });
  });

  it("rejects diagnostic identifiers and bounds phase/event cardinality", () => {
    const sample = {
      activeWaiting: 1,
      apiActiveWaiting: 0,
      waitEvents: { WALWrite: 1 },
      apiWaitEvents: {},
      waitEventTypes: { "IO/WALWrite": 1 },
      apiWaitEventTypes: {},
      blockingEventScopes: {}
    };
    expect(() => accumulatePostgresWaitDiagnostics(
      emptyPostgresWaitDiagnostics(), sample, "tenant/secret", 250, 10
    )).toThrow("postgres_wait_phase_invalid");

    let diagnostics = emptyPostgresWaitDiagnostics();
    for (let index = 0; index < 32; index += 1) {
      diagnostics = accumulatePostgresWaitDiagnostics(
        diagnostics, sample, `scenario_phase_${index}`, 250, 10
      );
    }
    expect(() => accumulatePostgresWaitDiagnostics(
      diagnostics, sample, "scenario_phase_overflow", 250, 10
    )).toThrow("postgres_wait_phase_cardinality_exceeded");

    const typedSample = (start: number, count: number) => ({
      activeWaiting: count,
      apiActiveWaiting: 0,
      waitEvents: Object.fromEntries(
        Array.from({ length: count }, (_, offset) => [`WaitEvent_${start + offset}`, 1])
      ),
      apiWaitEvents: {},
      waitEventTypes: Object.fromEntries(
        Array.from({ length: count }, (_, offset) => [`Extension/WaitEvent_${start + offset}`, 1])
      ),
      apiWaitEventTypes: {},
      blockingEventScopes: {}
    });
    let union = emptyPostgresWaitDiagnostics();
    union = accumulatePostgresWaitDiagnostics(
      union, typedSample(0, 32), "scenario_union", 250, 10
    );
    union = accumulatePostgresWaitDiagnostics(
      union, typedSample(32, 32), "scenario_union", 250, 10
    );
    expect(union.waitEventTypePeaksByPhase.scenario_union).toHaveProperty(
      "Extension/WaitEvent_63"
    );
    expect(() => accumulatePostgresWaitDiagnostics(
      union, typedSample(64, 1), "scenario_union", 250, 10
    )).toThrow("postgres_wait_event_cardinality_exceeded");
  });
});

function spawnPsql(container: string, sql: string) {
  return spawn("docker", [
    "exec", "-e", "PGAPPNAME=appsell_capacity_api", container,
    "psql", "-X", "-w", "-U", "appsell_runtime", "-d", "appsell",
    "-v", "ON_ERROR_STOP=1", "-Atc", sql
  ], { stdio: ["ignore", "pipe", "pipe"] });
}

async function waitForSqlCount(container: string, sql: string, minimum: number) {
  const deadline = Date.now() + 5_000;
  while (Date.now() < deadline) {
    const { stdout } = await execFileAsync("docker", [
      "exec", container, "psql", "-X", "-w", "-U", "postgres", "-d", "appsell",
      "-v", "ON_ERROR_STOP=1", "-Atc", sql
    ], { timeout: 3_000 });
    if (Number(stdout.trim()) >= minimum) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error("postgres_lock_probe_timeout");
}

async function terminateProbeSessions(container: string) {
  await execFileAsync("docker", [
    "exec", container, "psql", "-X", "-w", "-U", "postgres", "-d", "appsell",
    "-v", "ON_ERROR_STOP=1", "-Atc",
    "select pg_terminate_backend(pid) from pg_stat_activity " +
    "where pid<>pg_backend_pid() and application_name='appsell_capacity_api'"
  ], { timeout: 3_000 });
}

async function cleanupProbeSessions(
  container: string,
  processes: Array<ChildProcess | undefined>
) {
  const errors: unknown[] = [];
  try {
    await terminateProbeSessions(container);
  } catch (error) {
    errors.push(error);
  }
  for (const process of processes) {
    if (!process) continue;
    try {
      await stopChild(process);
    } catch (error) {
      errors.push(error);
    }
  }
  if (errors.length > 0) {
    throw new AggregateError(errors, "postgres_lock_probe_cleanup_failed");
  }
}

async function stopChild(process: ChildProcess) {
  if (await exitedWithin(process, 500)) return;
  process.kill("SIGTERM");
  if (await exitedWithin(process, 500)) return;
  process.kill("SIGKILL");
  if (!(await exitedWithin(process, 1_000))) {
    throw new Error("postgres_lock_probe_cleanup_timeout");
  }
}

function exitedWithin(process: ChildProcess, timeoutMs: number) {
  if (process.exitCode !== null || process.signalCode !== null) return Promise.resolve(true);
  return new Promise<boolean>((resolve) => {
    const timeout = setTimeout(() => {
      process.removeListener("exit", onExit);
      resolve(false);
    }, timeoutMs);
    const onExit = () => {
      clearTimeout(timeout);
      resolve(true);
    };
    process.once("exit", onExit);
  });
}
