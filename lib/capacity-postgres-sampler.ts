import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

export interface PostgresConnectionSample {
  maxConnections: number;
  reservedConnections: number;
  total: number;
  workload: number;
  runtime: number;
  api: number;
  active: number;
  idle: number;
  activeWaiting: number;
  lockWaiting: number;
  lwLockWaiting: number;
  bufferPinWaiting: number;
  clientWaiting: number;
  ioWaiting: number;
  ipcWaiting: number;
  activityWaiting: number;
  timeoutWaiting: number;
  extensionWaiting: number;
  otherWaiting: number;
  apiActiveWaiting: number;
  waitEvents: Record<string, number>;
  apiWaitEvents: Record<string, number>;
}

export interface PostgresWaitDiagnostics {
  waitEventPeaks: Record<string, number>;
  apiWaitEventPeaks: Record<string, number>;
  waitingSamplesByPhase: Record<string, number>;
  fullApiPoolWaitingSamples: number;
  fullApiPoolWaitingObservedMs: number;
  maxConsecutiveFullApiPoolWaitingSamples: number;
  maxConsecutiveFullApiPoolWaitingMs: number;
  currentConsecutiveFullApiPoolWaitingSamples: number;
  currentConsecutiveFullApiPoolWaitingMs: number;
}

type Executor = (
  file: string,
  arguments_: string[],
  options: { timeout: number }
) => Promise<{ stdout: string }>;

export async function samplePostgresConnections(
  containerId: string | undefined,
  executor: Executor = execFileAsync
): Promise<PostgresConnectionSample> {
  if (!containerId) throw new Error("postgres_container_missing");
  const { stdout } = await executor(
    "docker",
    [
      "exec", "-e", "PGAPPNAME=appsell_capacity_sampler", containerId,
      "psql", "-U", "postgres", "-d", "appsell", "-Atc",
      "with activity as materialized (" +
        "select pid,backend_type,usename,application_name,state,wait_event_type,wait_event " +
        "from pg_stat_activity where datname=current_database()" +
        "), exact_waits as (" +
        "select wait_event,count(*) event_count from activity " +
        "where pid<>pg_backend_pid() and state='active' and wait_event is not null " +
        "group by wait_event" +
        "), api_waits as (" +
        "select wait_event,count(*) event_count from activity " +
        "where pid<>pg_backend_pid() and usename='appsell_runtime' " +
        "and application_name='appsell_capacity_api' and state='active' " +
        "and wait_event is not null group by wait_event" +
        ") select current_setting('max_connections')::int," +
        "current_setting('superuser_reserved_connections')::int," +
        "count(*)," +
        "count(*) filter(where pid<>pg_backend_pid() and backend_type='client backend')," +
        "count(*) filter(where pid<>pg_backend_pid() and usename='appsell_runtime')," +
        "count(*) filter(where pid<>pg_backend_pid() and application_name='appsell_capacity_api')," +
        "count(*) filter(where pid<>pg_backend_pid() and state='active')," +
        "count(*) filter(where pid<>pg_backend_pid() and state='idle')," +
        "count(*) filter(where pid<>pg_backend_pid() and state='active' and wait_event is not null)," +
        "count(*) filter(where pid<>pg_backend_pid() and state='active' and wait_event_type='Lock')," +
        "count(*) filter(where pid<>pg_backend_pid() and state='active' and wait_event_type='LWLock')," +
        "count(*) filter(where pid<>pg_backend_pid() and state='active' and wait_event_type='BufferPin')," +
        "count(*) filter(where pid<>pg_backend_pid() and state='active' and wait_event_type='Client')," +
        "count(*) filter(where pid<>pg_backend_pid() and state='active' and wait_event_type='IO')," +
        "count(*) filter(where pid<>pg_backend_pid() and state='active' and wait_event_type='IPC')," +
        "count(*) filter(where pid<>pg_backend_pid() and state='active' and wait_event_type='Activity')," +
        "count(*) filter(where pid<>pg_backend_pid() and state='active' and wait_event_type='Timeout')," +
        "count(*) filter(where pid<>pg_backend_pid() and state='active' and wait_event_type='Extension')," +
        "count(*) filter(where pid<>pg_backend_pid() and state='active' and wait_event is not null " +
        "and coalesce(wait_event_type,'') not in " +
        "('Lock','LWLock','BufferPin','Client','IO','IPC','Activity','Timeout','Extension'))," +
        "count(*) filter(where pid<>pg_backend_pid() and usename='appsell_runtime' " +
        "and application_name='appsell_capacity_api' and state='active' " +
        "and wait_event is not null)," +
        "coalesce((select string_agg(wait_event || ':' || event_count::text,',' order by wait_event) " +
        "from exact_waits),'')," +
        "coalesce((select string_agg(wait_event || ':' || event_count::text,',' order by wait_event) " +
        "from api_waits),'') from activity"
    ],
    { timeout: 3_000 }
  );
  const fields = stdout.trim().split("|");
  const values = fields.slice(0, 20).map(Number);
  if (fields.length !== 22 || values.some((value) => !Number.isFinite(value))) {
    throw new Error("postgres_sample_invalid");
  }
  const waitEvents = parseWaitEvents(fields[20]!);
  const apiWaitEvents = parseWaitEvents(fields[21]!);
  const [
    maxConnections, reservedConnections, total, workload, runtime, api,
    active, idle, activeWaiting, lockWaiting, lwLockWaiting, bufferPinWaiting, clientWaiting,
    ioWaiting, ipcWaiting, activityWaiting, timeoutWaiting, extensionWaiting, otherWaiting,
    apiActiveWaiting
  ] = values;
  return {
    maxConnections: maxConnections!,
    reservedConnections: reservedConnections!,
    total: total!,
    workload: workload!,
    runtime: runtime!,
    api: api!,
    active: active!,
    idle: idle!,
    activeWaiting: activeWaiting!,
    lockWaiting: lockWaiting!,
    lwLockWaiting: lwLockWaiting!,
    bufferPinWaiting: bufferPinWaiting!,
    clientWaiting: clientWaiting!,
    ioWaiting: ioWaiting!,
    ipcWaiting: ipcWaiting!,
    activityWaiting: activityWaiting!,
    timeoutWaiting: timeoutWaiting!,
    extensionWaiting: extensionWaiting!,
    otherWaiting: otherWaiting!,
    apiActiveWaiting: apiActiveWaiting!,
    waitEvents,
    apiWaitEvents
  };
}

export function emptyPostgresWaitDiagnostics(): PostgresWaitDiagnostics {
  return {
    waitEventPeaks: {},
    apiWaitEventPeaks: {},
    waitingSamplesByPhase: {},
    fullApiPoolWaitingSamples: 0,
    fullApiPoolWaitingObservedMs: 0,
    maxConsecutiveFullApiPoolWaitingSamples: 0,
    maxConsecutiveFullApiPoolWaitingMs: 0,
    currentConsecutiveFullApiPoolWaitingSamples: 0,
    currentConsecutiveFullApiPoolWaitingMs: 0
  };
}

export function accumulatePostgresWaitDiagnostics(
  current: PostgresWaitDiagnostics,
  sample: Pick<PostgresConnectionSample, "activeWaiting" | "apiActiveWaiting" | "waitEvents" | "apiWaitEvents">,
  phase: string,
  sampleGapMs: number,
  apiPoolSize: number
): PostgresWaitDiagnostics {
  const waitEventPeaks = mergePeaks(current.waitEventPeaks, sample.waitEvents);
  const apiWaitEventPeaks = mergePeaks(current.apiWaitEventPeaks, sample.apiWaitEvents);
  const waitingSamplesByPhase = { ...current.waitingSamplesByPhase };
  if (sample.activeWaiting > 0) {
    waitingSamplesByPhase[phase] = (waitingSamplesByPhase[phase] ?? 0) + 1;
  }
  const fullApiPool = sample.apiActiveWaiting >= apiPoolSize;
  const consecutiveSamples = fullApiPool
    ? current.currentConsecutiveFullApiPoolWaitingSamples + 1 : 0;
  const evidencedGapMs = fullApiPool && current.currentConsecutiveFullApiPoolWaitingSamples > 0
    ? Math.max(0, sampleGapMs) : 0;
  const consecutiveMs = fullApiPool
    ? current.currentConsecutiveFullApiPoolWaitingMs + evidencedGapMs : 0;
  return {
    waitEventPeaks,
    apiWaitEventPeaks,
    waitingSamplesByPhase,
    fullApiPoolWaitingSamples: current.fullApiPoolWaitingSamples + (fullApiPool ? 1 : 0),
    fullApiPoolWaitingObservedMs: current.fullApiPoolWaitingObservedMs + evidencedGapMs,
    maxConsecutiveFullApiPoolWaitingSamples: Math.max(
      current.maxConsecutiveFullApiPoolWaitingSamples,
      consecutiveSamples
    ),
    maxConsecutiveFullApiPoolWaitingMs: Math.max(
      current.maxConsecutiveFullApiPoolWaitingMs,
      consecutiveMs
    ),
    currentConsecutiveFullApiPoolWaitingSamples: consecutiveSamples,
    currentConsecutiveFullApiPoolWaitingMs: consecutiveMs
  };
}

function mergePeaks(current: Record<string, number>, observed: Record<string, number>) {
  const merged = { ...current };
  for (const [event, count] of Object.entries(observed)) {
    merged[event] = Math.max(merged[event] ?? 0, count);
  }
  return merged;
}

function parseWaitEvents(value: string) {
  if (!value) return {};
  const events: Record<string, number> = {};
  for (const item of value.split(",")) {
    const match = /^([A-Za-z][A-Za-z0-9_]*):(\d+)$/.exec(item);
    if (!match) throw new Error("postgres_sample_invalid");
    const count = Number(match[2]);
    if (!Number.isSafeInteger(count) || count <= 0) throw new Error("postgres_sample_invalid");
    events[match[1]!] = count;
  }
  return events;
}
