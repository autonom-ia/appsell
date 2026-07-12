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
      "select current_setting('max_connections')::int," +
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
        "('Lock','LWLock','BufferPin','Client','IO','IPC','Activity','Timeout','Extension')) " +
        "from pg_stat_activity where datname=current_database()"
    ],
    { timeout: 3_000 }
  );
  const values = stdout.trim().split("|").map(Number);
  if (values.length !== 19 || values.some((value) => !Number.isFinite(value))) {
    throw new Error("postgres_sample_invalid");
  }
  const [
    maxConnections, reservedConnections, total, workload, runtime, api,
    active, idle, activeWaiting, lockWaiting, lwLockWaiting, bufferPinWaiting, clientWaiting,
    ioWaiting, ipcWaiting, activityWaiting, timeoutWaiting, extensionWaiting, otherWaiting
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
    otherWaiting: otherWaiting!
  };
}
