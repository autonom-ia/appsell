import { describe, expect, it, vi } from "vitest";

import {
  accumulatePostgresWaitDiagnostics,
  emptyPostgresWaitDiagnostics,
  samplePostgresConnections
} from "../lib/capacity-postgres-sampler";

describe("PostgreSQL capacity sampler", () => {
  it("separates the observer, runtime role and API application", async () => {
    const executor = vi.fn().mockResolvedValue({
      stdout: "24|3|11|10|9|8|6|4|8|0|1|0|1|1|1|1|1|1|1|7|ClientRead:1,WALWrite:7|WALWrite:7\n"
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
      apiWaitEvents: { WALWrite: 7 }
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
    expect(query).toContain("string_agg(wait_event");
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
      stdout: "24|3|11|10|9|8|6|4|8|0|1|0|1|1|1|1|1|1|1|7|WALWrite:not-a-number|WALWrite:7\n"
    });
    await expect(samplePostgresConnections("container", executor)).rejects.toThrow(
      "postgres_sample_invalid"
    );
  });

  it("attributes waits by phase and measures only gaps between consecutive full API-pool samples", () => {
    const sample = {
      activeWaiting: 10,
      apiActiveWaiting: 10,
      waitEvents: { ClientRead: 1, WALWrite: 9 },
      apiWaitEvents: { WALWrite: 9 }
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
      apiWaitEventPeaks: { WALWrite: 9 }
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
      apiWaitEvents: {}
    }, "scenario_owner_catalog", 250, 10);
    expect(reset).toMatchObject({
      waitingSamplesByPhase: { scenario_buyer_player: 2, scenario_owner_catalog: 1 },
      currentConsecutiveFullApiPoolWaitingSamples: 0,
      currentConsecutiveFullApiPoolWaitingMs: 0,
      waitEventPeaks: { ClientRead: 1, WALWrite: 9 }
    });
  });
});
