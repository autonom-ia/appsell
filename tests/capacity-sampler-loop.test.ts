import { describe, expect, it, vi } from "vitest";

import { runCapacitySamplerLoop } from "../lib/capacity-sampler-loop";

class FakeClock {
  time = 0;

  now = () => this.time;

  delay = async (milliseconds: number) => {
    this.time += milliseconds;
  };

  advance(milliseconds: number) {
    this.time += milliseconds;
  }
}

describe("capacity sampler loop", () => {
  it("starts 300ms probes on the 500ms monotonic deadlines", async () => {
    const clock = new FakeClock();
    const starts: number[] = [];
    let running = true;

    const metrics = await runCapacitySamplerLoop({
      intervalMs: 500,
      isRunning: () => running,
      getPhase: () => "steady",
      probe: async () => {
        starts.push(clock.now());
        clock.advance(300);
        if (starts.length === 2) running = false;
      },
      onError: vi.fn(),
      now: clock.now,
      delay: clock.delay
    });

    expect(starts).toEqual([0, 500]);
    expect(metrics).toEqual({
      starts: 2,
      successes: 2,
      errors: 0,
      skipped_deadlines: 0,
      max_gap_ms: 500,
      widest_gap_phase: "steady",
      max_duration_ms: 300,
      slowest_phase: "steady"
    });
  });

  it("starts an overdue 700ms probe immediately and counts the missed deadline", async () => {
    const clock = new FakeClock();
    const starts: number[] = [];
    let running = true;

    const metrics = await runCapacitySamplerLoop({
      intervalMs: 500,
      isRunning: () => running,
      getPhase: () => "slow",
      probe: async () => {
        starts.push(clock.now());
        clock.advance(700);
        if (starts.length === 2) running = false;
      },
      onError: vi.fn(),
      now: clock.now,
      delay: clock.delay
    });

    expect(starts).toEqual([0, 700]);
    expect(metrics.skipped_deadlines).toBe(1);
    expect(metrics.max_gap_ms).toBe(700);
    expect(metrics.max_duration_ms).toBe(700);
  });

  it("captures the phase at dispatch instead of after the probe", async () => {
    const clock = new FakeClock();
    let phase = "warmup";
    let running = true;
    const observed: string[] = [];

    const metrics = await runCapacitySamplerLoop({
      intervalMs: 500,
      isRunning: () => running,
      getPhase: () => phase,
      probe: async (dispatchedPhase) => {
        phase = "steady";
        clock.advance(250);
        observed.push(dispatchedPhase);
        running = false;
      },
      onError: vi.fn(),
      now: clock.now,
      delay: clock.delay
    });

    expect(observed).toEqual(["warmup"]);
    expect(metrics.slowest_phase).toBe("warmup");
  });

  it("records an error, calls onError and terminates", async () => {
    const clock = new FakeClock();
    const failure = new Error("probe failed");
    const onError = vi.fn();
    let attempts = 0;

    const metrics = await runCapacitySamplerLoop({
      intervalMs: 500,
      isRunning: () => true,
      getPhase: () => "failure",
      probe: async () => {
        attempts += 1;
        clock.advance(125);
        throw failure;
      },
      onError,
      now: clock.now,
      delay: clock.delay
    });

    expect(attempts).toBe(1);
    expect(onError).toHaveBeenCalledOnce();
    expect(onError).toHaveBeenCalledWith(failure);
    expect(metrics).toMatchObject({
      starts: 1,
      successes: 0,
      errors: 1,
      max_duration_ms: 125,
      slowest_phase: "failure"
    });
  });

  it("can continue after a reported error without overlapping probes", async () => {
    const clock = new FakeClock();
    let running = true;
    let attempts = 0;

    const metrics = await runCapacitySamplerLoop({
      intervalMs: 500,
      isRunning: () => running,
      getPhase: () => "portable",
      probe: async () => {
        attempts += 1;
        clock.advance(100);
        if (attempts === 1) throw new Error("transient");
        running = false;
      },
      onError: vi.fn(),
      continueAfterError: true,
      now: clock.now,
      delay: clock.delay
    });

    expect(metrics).toMatchObject({ starts: 2, successes: 1, errors: 1 });
  });

  it("never overlaps probes, including when work exceeds the interval", async () => {
    const clock = new FakeClock();
    let running = true;
    let active = 0;
    let maxActive = 0;
    let starts = 0;

    const metrics = await runCapacitySamplerLoop({
      intervalMs: 500,
      isRunning: () => running,
      getPhase: () => `probe_${starts}`,
      probe: async () => {
        starts += 1;
        active += 1;
        maxActive = Math.max(maxActive, active);
        await Promise.resolve();
        clock.advance(700);
        active -= 1;
        if (starts === 3) running = false;
      },
      onError: vi.fn(),
      now: clock.now,
      delay: clock.delay
    });

    expect(starts).toBe(3);
    expect(maxActive).toBe(1);
    expect(metrics).toMatchObject({
      starts: 3,
      successes: 3,
      errors: 0,
      skipped_deadlines: 2
    });
  });
});
