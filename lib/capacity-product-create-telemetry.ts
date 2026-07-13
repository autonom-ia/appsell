import { capacityLocalCiRuntimeEnabled } from "./capacity-local-ci-runtime";

const TRACE_ID = /^pc-(?:0[0-9]{2})$/;
const EVENT = "appsell_capacity_product_create_trace";
const CLIENT_TIMEOUT_BUDGET_MS = 5_000;

export type CapacityTraceLayer = "bff" | "api";
export type CapacityTraceOutcome = "success" | "http_error" | "exception";

const STAGES = {
  bff: ["session_auth", "form_csrf", "domain_fetch", "response"],
  api: ["internal_auth_replay", "pool_checkout_apply_context", "sql", "commit_response"]
} as const satisfies Record<CapacityTraceLayer, readonly string[]>;

export interface CapacityProductCreateTrace {
  schema_version: 1;
  event: typeof EVENT;
  layer: CapacityTraceLayer;
  trace: string;
  status: number;
  outcome: CapacityTraceOutcome;
  total_ms: number;
  late_completion: boolean;
  durations_ms: Record<string, number>;
}

export interface CapacityTraceLayerAggregate {
  traces: number;
  unique_traces: number;
  duplicate_traces: number;
  status_counts: Record<string, number>;
  outcome_counts: Record<string, number>;
  late_completions: number;
  stage_ms: Record<string, { count: number; p50: number; p95: number; p99: number; max: number }>;
}

export interface CapacityProductCreateTelemetryAggregate {
  schema_version: 1;
  enabled: boolean;
  scope: "capacity_product_create_only";
  status: "passed" | "failed";
  required_traces_per_layer: 100;
  parsed_json_events: number;
  rejected_trace_events: number;
  matched_trace_cardinality: number;
  bff: CapacityTraceLayerAggregate;
  api: CapacityTraceLayerAggregate;
}

export function capacityProductCreateTelemetryEnabled(
  environment: Record<string, string | undefined> = process.env
) {
  const raw = environment.APPSELL_CAPACITY_PRODUCT_CREATE_TELEMETRY;
  if (raw === undefined || raw === "" || raw === "false") return false;
  if (raw !== "true") throw new Error("invalid_capacity_product_create_telemetry_flag");
  const appEnvironment = environment.APPSELL_ENVIRONMENT;
  if ((environment.NODE_ENV === "production" && !capacityLocalCiRuntimeEnabled(environment)) ||
      (appEnvironment !== "local" && appEnvironment !== "ci")) {
    throw new Error("capacity_product_create_telemetry_forbidden_environment");
  }
  return true;
}

export function validCapacityProductCreateTrace(value: string | null | undefined): value is string {
  return typeof value === "string" && TRACE_ID.test(value);
}

export function emitCapacityProductCreateTrace(
  event: CapacityProductCreateTrace,
  sink: (line: string) => void = console.log
) {
  if (!isCapacityProductCreateTrace(event)) throw new Error("invalid_capacity_trace_event");
  sink(JSON.stringify(event));
}

export function parseCapacityProductCreateTraceLine(line: string): CapacityProductCreateTrace | null {
  let value: unknown;
  try {
    value = JSON.parse(line);
  } catch {
    return null;
  }
  if (!isRecord(value) || value.event !== EVENT) return null;
  if (!isCapacityProductCreateTrace(value)) throw new Error("rejected_capacity_trace_event");
  return value;
}

export function aggregateCapacityProductCreateTraces(
  bff: CapacityProductCreateTrace[],
  api: CapacityProductCreateTrace[],
  rejectedTraceEvents = 0,
  enabled = true
): CapacityProductCreateTelemetryAggregate {
  const bffAggregate = aggregateLayer(bff, "bff");
  const apiAggregate = aggregateLayer(api, "api");
  const bffIds = new Set(bff.map((event) => event.trace));
  const apiIds = new Set(api.map((event) => event.trace));
  const matched = [...bffIds].filter((trace) => apiIds.has(trace)).length;
  const passed = enabled && rejectedTraceEvents === 0 && matched === 100 &&
    bffAggregate.traces === 100 && bffAggregate.unique_traces === 100 &&
    apiAggregate.traces === 100 && apiAggregate.unique_traces === 100;
  return {
    schema_version: 1,
    enabled,
    scope: "capacity_product_create_only",
    status: passed ? "passed" : "failed",
    required_traces_per_layer: 100,
    parsed_json_events: bff.length + api.length,
    rejected_trace_events: rejectedTraceEvents,
    matched_trace_cardinality: matched,
    bff: bffAggregate,
    api: apiAggregate
  };
}

export function traceTotalMs(durations: Record<string, number>) {
  return rounded(Object.values(durations).reduce((sum, value) => sum + value, 0));
}

export function traceLateCompletion(totalMs: number) {
  return totalMs >= CLIENT_TIMEOUT_BUDGET_MS;
}

function aggregateLayer(events: CapacityProductCreateTrace[], layer: CapacityTraceLayer) {
  const expectedStages = STAGES[layer];
  const ids = new Set<string>();
  const statuses: Record<string, number> = {};
  const outcomes: Record<string, number> = {};
  const stageValues = Object.fromEntries(expectedStages.map((stage) => [stage, [] as number[]]));
  for (const event of events) {
    ids.add(event.trace);
    statuses[String(event.status)] = (statuses[String(event.status)] ?? 0) + 1;
    outcomes[event.outcome] = (outcomes[event.outcome] ?? 0) + 1;
    for (const stage of expectedStages) stageValues[stage]!.push(event.durations_ms[stage]!);
  }
  return {
    traces: events.length,
    unique_traces: ids.size,
    duplicate_traces: events.length - ids.size,
    status_counts: statuses,
    outcome_counts: outcomes,
    late_completions: events.filter((event) => event.late_completion).length,
    stage_ms: Object.fromEntries(expectedStages.map((stage) => {
      const values = stageValues[stage]!;
      return [stage, {
        count: values.length,
        p50: percentile(values, 50),
        p95: percentile(values, 95),
        p99: percentile(values, 99),
        max: rounded(Math.max(...values, 0))
      }];
    }))
  };
}

function isCapacityProductCreateTrace(value: unknown): value is CapacityProductCreateTrace {
  if (!isRecord(value)) return false;
  const keys = Object.keys(value).sort();
  const allowed = [
    "durations_ms", "event", "late_completion", "layer", "outcome",
    "schema_version", "status", "total_ms", "trace"
  ].sort();
  if (keys.length !== allowed.length || keys.some((key, index) => key !== allowed[index])) return false;
  if (value.schema_version !== 1 || value.event !== EVENT ||
      (value.layer !== "bff" && value.layer !== "api") ||
      !validCapacityProductCreateTrace(typeof value.trace === "string" ? value.trace : null) ||
      !Number.isSafeInteger(value.status) || (value.status as number) < 100 || (value.status as number) > 599 ||
      !["success", "http_error", "exception"].includes(String(value.outcome)) ||
      typeof value.late_completion !== "boolean" || !validDuration(value.total_ms) ||
      !isRecord(value.durations_ms)) return false;
  if (value.late_completion !== traceLateCompletion(value.total_ms as number) ||
      (value.outcome === "success" && (value.status as number) >= 400) ||
      (value.outcome === "http_error" && (value.status as number) < 400)) return false;
  const stages = STAGES[value.layer];
  const durations = value.durations_ms as Record<string, unknown>;
  const durationKeys = Object.keys(durations).sort();
  const expected = [...stages].sort();
  return durationKeys.length === expected.length &&
    durationKeys.every((key, index) => key === expected[index]) &&
    durationKeys.every((key) => validDuration(durations[key])) &&
    Math.abs(durationKeys.reduce((sum, key) => sum + Number(durations[key]), 0) -
      Number(value.total_ms)) <= 1;
}

function validDuration(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 900_000;
}

function percentile(values: number[], requested: number) {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  return rounded(sorted[Math.max(0, Math.ceil(requested / 100 * sorted.length) - 1)] ?? 0);
}

function rounded(value: number) {
  return Math.round(value * 100) / 100;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
