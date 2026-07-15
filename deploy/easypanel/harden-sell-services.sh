#!/usr/bin/env bash
set -euo pipefail

confirmation="${CONFIRM_SELL_AUTONOMIA_HARDENING:-}"
[[ "$confirmation" == "sell-autonomia-v1" ]] || {
  echo "set CONFIRM_SELL_AUTONOMIA_HARDENING=sell-autonomia-v1" >&2
  exit 2
}

global_network="${EASYPANEL_GLOBAL_NETWORK:-easypanel}"
project_network="${SELL_PROJECT_NETWORK:-easypanel-sell-autonomia}"
verify_script="$(dirname "$0")/verify-sell-isolation.sh"
global_network_id="$(docker network inspect "$global_network" --format '{{.Id}}')"
project_network_id="$(docker network inspect "$project_network" --format '{{.Id}}')"

services=(
  sell-autonomia_postgres
  sell-autonomia_redis
  sell-autonomia_storage
)
declare -A health_commands=(
  [sell-autonomia_postgres]='pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
  [sell-autonomia_redis]='redis-cli -a "$REDIS_PASSWORD" ping 2>/dev/null | grep -qx PONG'
  [sell-autonomia_storage]='curl -fsS http://127.0.0.1:9000/minio/health/ready >/dev/null'
)
declare -A cpu_limits=(
  [sell-autonomia_postgres]='1.0'
  [sell-autonomia_redis]='0.5'
  [sell-autonomia_storage]='0.75'
)
declare -A cpu_reservations=(
  [sell-autonomia_postgres]='0.10'
  [sell-autonomia_redis]='0.05'
  [sell-autonomia_storage]='0.10'
)
declare -A memory_limits=(
  [sell-autonomia_postgres]='1024M'
  [sell-autonomia_redis]='512M'
  [sell-autonomia_storage]='1024M'
)
declare -A memory_reservations=(
  [sell-autonomia_postgres]='256M'
  [sell-autonomia_redis]='128M'
  [sell-autonomia_storage]='256M'
)
declare -A cpu_limits_nano=(
  [sell-autonomia_postgres]='1000000000'
  [sell-autonomia_redis]='500000000'
  [sell-autonomia_storage]='750000000'
)
declare -A cpu_reservations_nano=(
  [sell-autonomia_postgres]='100000000'
  [sell-autonomia_redis]='50000000'
  [sell-autonomia_storage]='100000000'
)
declare -A memory_limits_bytes=(
  [sell-autonomia_postgres]='1073741824'
  [sell-autonomia_redis]='536870912'
  [sell-autonomia_storage]='1073741824'
)
declare -A memory_reservations_bytes=(
  [sell-autonomia_postgres]='268435456'
  [sell-autonomia_redis]='134217728'
  [sell-autonomia_storage]='268435456'
)
declare -A remove_global_network=()
declare -A update_required=()
declare -A previous_task_id=()

service_matches_target() {
  local service="$1"
  local cpu_limit cpu_reservation memory_limit memory_reservation
  local health_test_length health_test_mode health_test_command
  local health_interval health_timeout health_retries health_start_period
  cpu_limit="$(docker service inspect "$service" --format '{{.Spec.TaskTemplate.Resources.Limits.NanoCPUs}}')"
  cpu_reservation="$(docker service inspect "$service" --format '{{.Spec.TaskTemplate.Resources.Reservations.NanoCPUs}}')"
  memory_limit="$(docker service inspect "$service" --format '{{.Spec.TaskTemplate.Resources.Limits.MemoryBytes}}')"
  memory_reservation="$(docker service inspect "$service" --format '{{.Spec.TaskTemplate.Resources.Reservations.MemoryBytes}}')"
  health_test_length="$(docker service inspect "$service" --format '{{len .Spec.TaskTemplate.ContainerSpec.Healthcheck.Test}}' 2>/dev/null || true)"
  health_test_mode="$(docker service inspect "$service" --format '{{index .Spec.TaskTemplate.ContainerSpec.Healthcheck.Test 0}}' 2>/dev/null || true)"
  health_test_command="$(docker service inspect "$service" --format '{{index .Spec.TaskTemplate.ContainerSpec.Healthcheck.Test 1}}' 2>/dev/null || true)"
  health_interval="$(docker service inspect "$service" --format '{{.Spec.TaskTemplate.ContainerSpec.Healthcheck.Interval}}' 2>/dev/null || true)"
  health_timeout="$(docker service inspect "$service" --format '{{.Spec.TaskTemplate.ContainerSpec.Healthcheck.Timeout}}' 2>/dev/null || true)"
  health_retries="$(docker service inspect "$service" --format '{{.Spec.TaskTemplate.ContainerSpec.Healthcheck.Retries}}' 2>/dev/null || true)"
  health_start_period="$(docker service inspect "$service" --format '{{.Spec.TaskTemplate.ContainerSpec.Healthcheck.StartPeriod}}' 2>/dev/null || true)"
  [[ "$cpu_limit" == "${cpu_limits_nano[$service]}" &&
     "$cpu_reservation" == "${cpu_reservations_nano[$service]}" &&
     "$memory_limit" == "${memory_limits_bytes[$service]}" &&
     "$memory_reservation" == "${memory_reservations_bytes[$service]}" &&
     "$health_test_length" == "2" && "$health_test_mode" == "CMD-SHELL" &&
     "$health_test_command" == "${health_commands[$service]}" &&
     "$health_interval" == "15s" && "$health_timeout" == "5s" &&
     "$health_retries" == "5" && "$health_start_period" == "20s" ]]
}

# Fail before the first mutation if the isolated fleet or its verifier is incomplete.
[[ -x "$verify_script" ]] || { echo "missing executable verifier: $verify_script" >&2; exit 1; }
for service in "${services[@]}"; do
  docker service inspect "$service" >/dev/null
  update_state="$(docker service inspect "$service" --format '{{if .UpdateStatus}}{{.UpdateStatus.State}}{{end}}')"
  [[ -z "$update_state" || "$update_state" == "completed" ]] || {
    echo "$service update state=$update_state before hardening" >&2
    exit 1
  }
  desired_replicas="$(docker service inspect "$service" --format '{{.Spec.Mode.Replicated.Replicas}}')"
  [[ "$desired_replicas" == "1" ]] || { echo "$service desired replicas=$desired_replicas" >&2; exit 1; }
  ports="$(docker service inspect "$service" --format '{{json .Endpoint.Spec.Ports}}')"
  [[ "$ports" == "null" || "$ports" == "[]" ]] || { echo "$service has published ports" >&2; exit 1; }
  mapfile -t networks < <(docker service inspect "$service" --format '{{range .Spec.TaskTemplate.Networks}}{{println .Target}}{{end}}' | sed '/^$/d')
  [[ " ${networks[*]} " == *" $project_network_id "* ]] || { echo "$service missing project network" >&2; exit 1; }
  for network_id in "${networks[@]}"; do
    [[ "$network_id" == "$project_network_id" || "$network_id" == "$global_network_id" ]] || {
      echo "$service has unexpected network before hardening" >&2
      exit 1
    }
  done
  [[ "${#networks[@]}" -le 2 ]] || { echo "$service has too many networks" >&2; exit 1; }
  mapfile -t running_tasks < <(docker service ps "$service" --filter desired-state=running --no-trunc --format '{{.ID}}')
  [[ "${#running_tasks[@]}" -eq 1 ]] || { echo "$service running tasks=${#running_tasks[@]} before hardening" >&2; exit 1; }
  previous_task_id[$service]="${running_tasks[0]}"
  remove_global_network[$service]="false"
  update_required[$service]="false"
  if [[ " ${networks[*]} " == *" $global_network_id "* ]]; then
    remove_global_network[$service]="true"
    update_required[$service]="true"
  fi
  if ! service_matches_target "$service"; then
    update_required[$service]="true"
  fi
done

for service in "${services[@]}"; do
  if [[ "${update_required[$service]}" == "false" ]]; then
    echo "$service hardening=already-current"
    continue
  fi
  network_args=()
  if [[ "${remove_global_network[$service]}" == "true" ]]; then
    network_args=(--network-rm "$global_network")
  fi

  docker service update --detach=false \
    "${network_args[@]}" \
    --limit-cpu "${cpu_limits[$service]}" \
    --reserve-cpu "${cpu_reservations[$service]}" \
    --limit-memory "${memory_limits[$service]}" \
    --reserve-memory "${memory_reservations[$service]}" \
    --health-cmd "${health_commands[$service]}" \
    --health-interval 15s \
    --health-timeout 5s \
    --health-retries 5 \
    --health-start-period 20s \
    "$service" >/dev/null
  echo "$service hardening=applied"
done

converged=false
for _ in $(seq 1 60); do
  ready=true
  for service in "${services[@]}"; do
    update_state="$(docker service inspect "$service" --format '{{if .UpdateStatus}}{{.UpdateStatus.State}}{{end}}')"
    if [[ "${update_required[$service]}" == "true" ]]; then
      [[ "$update_state" == "completed" ]] || ready=false
    else
      [[ -z "$update_state" || "$update_state" == "completed" ]] || ready=false
    fi
    replicas="$(docker service ls --filter "name=$service" --format '{{.Replicas}}')"
    mapfile -t running_tasks < <(docker service ps "$service" --filter desired-state=running --no-trunc --format '{{.ID}}')
    [[ "${#running_tasks[@]}" -eq 1 ]] || { ready=false; continue; }
    if [[ "${update_required[$service]}" == "true" && "${running_tasks[0]}" == "${previous_task_id[$service]}" ]]; then
      ready=false
      continue
    fi
    container_id="$(docker ps --filter "label=com.docker.swarm.task.id=${running_tasks[0]}" --format '{{.ID}}' | head -1)"
    health="starting"
    [[ -n "$container_id" ]] && health="$(docker inspect "$container_id" --format '{{.State.Health.Status}}' 2>/dev/null || true)"
    [[ "$replicas" == "1/1" && "$health" == "healthy" ]] || ready=false
  done
  if [[ "$ready" == "true" ]]; then
    converged=true
    break
  fi
  sleep 2
done
[[ "$converged" == "true" ]] || { echo "Sell hardening did not converge on healthy current tasks" >&2; exit 1; }

"$verify_script"
