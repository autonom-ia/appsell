import { describe, expect, it } from "vitest";

import {
  calculatePostgresHeadroom,
  postgresHeadroomFailures,
  type PostgresCapacitySample
} from "../lib/capacity-postgres-headroom";

const baseline: PostgresCapacitySample = {
  rawConnections: 13,
  workloadConnections: 12,
  runtimeConnections: 10,
  apiConnections: 10,
  maxConnections: 24,
  reservedConnections: 3,
  sampleErrors: 0,
  maxSampleGapMs: 1_999,
  activeWaitingConnections: 5,
  lockWaitingConnections: 0,
  bufferPinWaitingConnections: 0,
  otherWaitingConnections: 0,
  samplesWithActiveWaiting: 25,
  samples: 100
};

describe("PostgreSQL capacity headroom", () => {
  it("accepts the exact 9-slot and >=40% boundary", () => {
    expect(calculatePostgresHeadroom(baseline)).toEqual({
      usableConnections: 21,
      headroomConnections: 9,
      headroomPercent: 42.86
    });
    expect(postgresHeadroomFailures(baseline)).toEqual([]);
  });

  it("rejects eight slots even when the percentage remains above 40", () => {
    expect(postgresHeadroomFailures({ ...baseline, workloadConnections: 13 })).toContain(
      "headroom_below_budget"
    );
  });

  it("rejects 39.9% headroom independently of absolute slots", () => {
    const sample = {
      ...baseline,
      maxConnections: 1_003,
      reservedConnections: 3,
      workloadConnections: 601
    };
    expect(calculatePostgresHeadroom(sample).headroomPercent).toBe(39.9);
    expect(postgresHeadroomFailures(sample)).toContain("headroom_below_budget");
  });

  it("fails closed on any sampler error or excessive sample gap", () => {
    expect(postgresHeadroomFailures({ ...baseline, sampleErrors: 1 })).toContain("sampler_invalid");
    expect(postgresHeadroomFailures({ ...baseline, maxSampleGapMs: 2_001 })).toContain(
      "sampler_gap_exceeded"
    );
  });

  it("rejects lock waits and any excess in the bounded non-lock wait budget", () => {
    expect(postgresHeadroomFailures({ ...baseline, lockWaitingConnections: 1 })).toContain(
      "postgres_lock_wait_observed"
    );
    expect(postgresHeadroomFailures({ ...baseline, bufferPinWaitingConnections: 1 })).toContain(
      "postgres_buffer_pin_wait_observed"
    );
    expect(postgresHeadroomFailures({ ...baseline, activeWaitingConnections: 6 })).toContain(
      "postgres_wait_budget_exceeded"
    );
    expect(postgresHeadroomFailures({ ...baseline, otherWaitingConnections: 1 })).toContain(
      "postgres_unclassified_wait_observed"
    );
    expect(postgresHeadroomFailures({ ...baseline, samplesWithActiveWaiting: 26 })).toContain(
      "postgres_wait_budget_exceeded"
    );
  });

  it("normalizes non-lock waits by samples instead of total workload duration", () => {
    expect(postgresHeadroomFailures({
      ...baseline,
      samplesWithActiveWaiting: 50,
      samples: 200
    })).not.toContain("postgres_wait_budget_exceeded");
  });
});
