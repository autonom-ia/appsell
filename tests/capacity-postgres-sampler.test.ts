import { describe, expect, it, vi } from "vitest";

import { samplePostgresConnections } from "../lib/capacity-postgres-sampler";

describe("PostgreSQL capacity sampler", () => {
  it("separates the observer, runtime role and API application", async () => {
    const executor = vi.fn().mockResolvedValue({ stdout: "24|3|11|10|9|8|6|4|0\n" });
    await expect(samplePostgresConnections("container", executor)).resolves.toEqual({
      maxConnections: 24,
      reservedConnections: 3,
      total: 11,
      workload: 10,
      runtime: 9,
      api: 8,
      active: 6,
      idle: 4,
      activeWaiting: 0
    });
    expect(executor.mock.calls[0]?.[1]).toContain("PGAPPNAME=appsell_capacity_sampler");
    expect(executor.mock.calls[0]?.[1].at(-1)).toContain("pid<>pg_backend_pid()");
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
});
