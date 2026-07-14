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
  waitEventTypes: Record<string, number>;
  apiWaitEventTypes: Record<string, number>;
  blockingEventScopes: Record<string, number>;
}

export interface PostgresWaitDiagnostics {
  waitEventPeaks: Record<string, number>;
  apiWaitEventPeaks: Record<string, number>;
  waitingSamplesByPhase: Record<string, number>;
  waitEventTypePeaksByPhase: Record<string, Record<string, number>>;
  apiWaitEventTypePeaksByPhase: Record<string, Record<string, number>>;
  blockingEventScopePeaksByPhase: Record<string, Record<string, number>>;
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
        "select wait_event_type,wait_event,count(*) event_count from activity " +
        "where pid<>pg_backend_pid() and state='active' and wait_event is not null " +
        "group by wait_event_type,wait_event" +
        "), api_waits as (" +
        "select wait_event_type,wait_event,count(*) event_count from activity " +
        "where pid<>pg_backend_pid() and usename='appsell_runtime' " +
        "and application_name='appsell_capacity_api' and state='active' " +
        "and wait_event is not null group by wait_event_type,wait_event" +
        "), lock_waiters as materialized (" +
        "select pid,backend_type,usename,application_name,state,wait_event_type,wait_event " +
        "from activity where pid<>pg_backend_pid() and state='active' " +
        "and wait_event_type='Lock' and wait_event is not null" +
        "), blocking_relations as (" +
        "select waiter.wait_event_type,waiter.wait_event," +
        "case when waiter.backend_type='autovacuum worker' then 'autovacuum' " +
        "when waiter.usename='appsell_runtime' and " +
        "waiter.application_name='appsell_capacity_api' then 'api' " +
        "when waiter.usename='appsell_runtime' then 'runtime_other' else 'other' end waiter_scope," +
        "case when blocker.pid is null then 'unknown' " +
        "when blocker.backend_type='autovacuum worker' then 'autovacuum' " +
        "when blocker.usename='appsell_runtime' and " +
        "blocker.application_name='appsell_capacity_api' then 'api' " +
        "when blocker.usename='appsell_runtime' then 'runtime_other' else 'other' end blocker_scope," +
        "count(*) relation_count from lock_waiters waiter cross join lateral " +
        "unnest(pg_blocking_pids(waiter.pid)) blocker_pid " +
        "left join activity blocker on blocker.pid=blocker_pid " +
        "group by waiter.wait_event_type,waiter.wait_event,waiter_scope,blocker_scope" +
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
        "coalesce((select string_agg(wait_event_type || '/' || wait_event || ':' || " +
        "event_count::text,',' order by wait_event_type,wait_event) " +
        "from exact_waits),'')," +
        "coalesce((select string_agg(wait_event_type || '/' || wait_event || ':' || " +
        "event_count::text,',' order by wait_event_type,wait_event) " +
        "from api_waits),'')," +
        "coalesce((select string_agg(wait_event_type || '/' || wait_event || '@' || " +
        "waiter_scope || '>' || blocker_scope || ':' || relation_count::text,',' " +
        "order by wait_event_type,wait_event,waiter_scope,blocker_scope) " +
        "from blocking_relations),'') from activity"
    ],
    { timeout: 3_000 }
  );
  const fields = stdout.trim().split("|");
  const values = fields.slice(0, 20).map(Number);
  if (fields.length !== 23 || values.some((value) => !Number.isFinite(value))) {
    throw new Error("postgres_sample_invalid");
  }
  const waitEventTypes = parseWaitEventTypes(fields[20]!);
  const apiWaitEventTypes = parseWaitEventTypes(fields[21]!);
  const blockingEventScopes = parseBlockingEventScopes(fields[22]!);
  const waitEvents = collapseWaitEventTypes(waitEventTypes);
  const apiWaitEvents = collapseWaitEventTypes(apiWaitEventTypes);
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
    apiWaitEvents,
    waitEventTypes,
    apiWaitEventTypes,
    blockingEventScopes
  };
}

export function emptyPostgresWaitDiagnostics(): PostgresWaitDiagnostics {
  return {
    waitEventPeaks: {},
    apiWaitEventPeaks: {},
    waitingSamplesByPhase: {},
    waitEventTypePeaksByPhase: {},
    apiWaitEventTypePeaksByPhase: {},
    blockingEventScopePeaksByPhase: {},
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
  sample: Pick<PostgresConnectionSample,
    | "activeWaiting" | "apiActiveWaiting" | "waitEvents" | "apiWaitEvents"
    | "waitEventTypes" | "apiWaitEventTypes" | "blockingEventScopes">,
  phase: string,
  sampleGapMs: number,
  apiPoolSize: number
): PostgresWaitDiagnostics {
  if (!/^[a-z][a-z0-9_]{0,63}$/.test(phase)) {
    throw new Error("postgres_wait_phase_invalid");
  }
  const waitEventPeaks = mergePeaks(current.waitEventPeaks, sample.waitEvents);
  const apiWaitEventPeaks = mergePeaks(current.apiWaitEventPeaks, sample.apiWaitEvents);
  const waitingSamplesByPhase = { ...current.waitingSamplesByPhase };
  if (sample.activeWaiting > 0) {
    waitingSamplesByPhase[phase] = (waitingSamplesByPhase[phase] ?? 0) + 1;
  }
  const waitEventTypePeaksByPhase = mergePhasePeaks(
    current.waitEventTypePeaksByPhase,
    phase,
    sample.waitEventTypes
  );
  const apiWaitEventTypePeaksByPhase = mergePhasePeaks(
    current.apiWaitEventTypePeaksByPhase,
    phase,
    sample.apiWaitEventTypes
  );
  const blockingEventScopePeaksByPhase = mergePhasePeaks(
    current.blockingEventScopePeaksByPhase,
    phase,
    sample.blockingEventScopes
  );
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
    waitEventTypePeaksByPhase,
    apiWaitEventTypePeaksByPhase,
    blockingEventScopePeaksByPhase,
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

function mergePhasePeaks(
  current: Record<string, Record<string, number>>,
  phase: string,
  observed: Record<string, number>
) {
  if (Object.keys(observed).length === 0) return current;
  if (!(phase in current) && Object.keys(current).length >= 32) {
    throw new Error("postgres_wait_phase_cardinality_exceeded");
  }
  const phasePeaks = mergePeaks(current[phase] ?? {}, observed);
  if (Object.keys(phasePeaks).length > 64) {
    throw new Error("postgres_wait_event_cardinality_exceeded");
  }
  return { ...current, [phase]: phasePeaks };
}

function parseWaitEventTypes(value: string) {
  if (!value) return {};
  const events: Record<string, number> = {};
  const items = value.split(",");
  if (items.length > 64) throw new Error("postgres_sample_invalid");
  for (const item of items) {
    const match = /^([A-Za-z][A-Za-z0-9_]{0,63}\/[A-Za-z][A-Za-z0-9_]{0,63}):(\d+)$/.exec(item);
    if (!match) throw new Error("postgres_sample_invalid");
    const count = Number(match[2]);
    if (!Number.isSafeInteger(count) || count <= 0) throw new Error("postgres_sample_invalid");
    if (match[1] in events) throw new Error("postgres_sample_invalid");
    events[match[1]!] = count;
  }
  return events;
}

function collapseWaitEventTypes(typedEvents: Record<string, number>) {
  const events: Record<string, number> = {};
  for (const [typedEvent, count] of Object.entries(typedEvents)) {
    const event = typedEvent.slice(typedEvent.indexOf("/") + 1);
    events[event] = (events[event] ?? 0) + count;
  }
  return events;
}

function parseBlockingEventScopes(value: string) {
  if (!value) return {};
  const scopes: Record<string, number> = {};
  const items = value.split(",");
  if (items.length > 64) throw new Error("postgres_sample_invalid");
  for (const item of items) {
    const match = /^(Lock\/[A-Za-z][A-Za-z0-9_]{0,63})@(api|runtime_other|autovacuum|other)>(api|runtime_other|autovacuum|other|unknown):(\d+)$/.exec(item);
    if (!match) throw new Error("postgres_sample_invalid");
    const key = `${match[1]}@${match[2]}>${match[3]}`;
    const count = Number(match[4]);
    if (!Number.isSafeInteger(count) || count <= 0 || key in scopes) {
      throw new Error("postgres_sample_invalid");
    }
    scopes[key] = count;
  }
  return scopes;
}
