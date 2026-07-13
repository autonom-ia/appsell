export interface CapacitySamplerLoopMetrics {
  starts: number;
  successes: number;
  errors: number;
  skipped_deadlines: number;
  max_gap_ms: number;
  widest_gap_phase: string | null;
  max_duration_ms: number;
  slowest_phase: string | null;
}

export interface CapacitySamplerLoopOptions {
  intervalMs: number;
  isRunning: () => boolean;
  getPhase: () => string;
  probe: (phase: string) => Promise<void>;
  onError: (error: unknown) => void | Promise<void>;
  continueAfterError?: boolean;
  now?: () => number;
  delay?: (milliseconds: number) => Promise<void>;
}

const defaultDelay = (milliseconds: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, milliseconds));

/**
 * Runs probes against monotonic deadlines without ever overlapping them.
 *
 * A late probe does not start concurrent catch-up work. Instead, every elapsed
 * deadline is counted and one replacement probe starts immediately. The next
 * future deadline remains anchored to the original monotonic schedule.
 */
export async function runCapacitySamplerLoop(
  options: CapacitySamplerLoopOptions
): Promise<CapacitySamplerLoopMetrics> {
  if (!Number.isFinite(options.intervalMs) || options.intervalMs <= 0) {
    throw new Error("capacity_sampler_interval_invalid");
  }

  const now = options.now ?? performance.now.bind(performance);
  const delay = options.delay ?? defaultDelay;
  const metrics: CapacitySamplerLoopMetrics = {
    starts: 0,
    successes: 0,
    errors: 0,
    skipped_deadlines: 0,
    max_gap_ms: 0,
    widest_gap_phase: null,
    max_duration_ms: 0,
    slowest_phase: null
  };
  let nextDeadline = readNow(now);
  let previousStart: number | undefined;

  while (options.isRunning()) {
    let dispatchAt = readNow(now);
    while (dispatchAt < nextDeadline) {
      await delay(nextDeadline - dispatchAt);
      if (!options.isRunning()) return metrics;
      dispatchAt = readNow(now);
    }

    while (nextDeadline <= dispatchAt) nextDeadline += options.intervalMs;

    if (!options.isRunning()) return metrics;

    const phase = options.getPhase();
    const startedAt = dispatchAt;
    if (previousStart !== undefined) {
      const gap = startedAt - previousStart;
      const deadlineToleranceMs = Math.min(10, options.intervalMs * 0.1);
      if (gap > options.intervalMs + deadlineToleranceMs) {
        metrics.skipped_deadlines += Math.max(1, Math.ceil(gap / options.intervalMs) - 1);
      }
      if (metrics.widest_gap_phase === null || gap > metrics.max_gap_ms) {
        metrics.max_gap_ms = gap;
        metrics.widest_gap_phase = phase;
      }
    }
    previousStart = startedAt;
    metrics.starts += 1;
    let succeeded = false;

    try {
      await options.probe(phase);
      metrics.successes += 1;
      succeeded = true;
    } catch (error) {
      metrics.errors += 1;
      recordDuration(metrics, phase, readNow(now) - startedAt);
      try {
        await options.onError(error);
      } catch {
        // The probe failure remains authoritative; a reporting failure must not
        // restart the sampler or obscure its terminal metrics.
      }
      if (!options.continueAfterError) return metrics;
    }

    if (succeeded) {
      recordDuration(metrics, phase, readNow(now) - startedAt);
    }
  }

  return metrics;
}

function readNow(now: () => number): number {
  const value = now();
  if (!Number.isFinite(value)) throw new Error("capacity_sampler_clock_invalid");
  return value;
}

function recordDuration(metrics: CapacitySamplerLoopMetrics, phase: string, duration: number) {
  const boundedDuration = Math.max(0, duration);
  if (metrics.slowest_phase === null || boundedDuration > metrics.max_duration_ms) {
    metrics.max_duration_ms = boundedDuration;
    metrics.slowest_phase = phase;
  }
}
