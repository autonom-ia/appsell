export interface PostgresHeadroomSample {
  rawConnections: number;
  workloadConnections: number;
  runtimeConnections: number;
  apiConnections: number;
  maxConnections: number;
  reservedConnections: number;
  sampleErrors: number;
  maxSampleGapMs: number;
}

export interface PostgresHeadroom {
  usableConnections: number;
  headroomConnections: number;
  headroomPercent: number;
}

export function calculatePostgresHeadroom(sample: PostgresHeadroomSample): PostgresHeadroom {
  const usableConnections = sample.maxConnections - sample.reservedConnections;
  const headroomConnections = usableConnections - sample.workloadConnections;
  const headroomPercent = usableConnections > 0
    ? Math.round(headroomConnections / usableConnections * 10_000) / 100
    : 0;
  return { usableConnections, headroomConnections, headroomPercent };
}

export function postgresHeadroomFailures(sample: PostgresHeadroomSample): string[] {
  const failures: string[] = [];
  if (sample.sampleErrors > 0) failures.push("sampler_invalid");
  if (sample.maxSampleGapMs > 2_000) failures.push("sampler_gap_exceeded");
  if (sample.maxConnections !== 24 || sample.reservedConnections !== 3) {
    failures.push("server_budget_mismatch");
  }
  if (sample.rawConnections <= 1 || sample.rawConnections > 13) {
    failures.push("raw_connections_out_of_budget");
  }
  if (sample.workloadConnections <= 0 || sample.workloadConnections > 12) {
    failures.push("workload_connections_out_of_budget");
  }
  if (sample.runtimeConnections > 10 || sample.apiConnections > 10) {
    failures.push("runtime_pool_out_of_budget");
  }
  const headroom = calculatePostgresHeadroom(sample);
  if (headroom.headroomConnections < 9 || headroom.headroomPercent < 40) {
    failures.push("headroom_below_budget");
  }
  return failures;
}
