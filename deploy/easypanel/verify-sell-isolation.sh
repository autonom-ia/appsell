#!/usr/bin/env bash
set -euo pipefail

project_network="${SELL_PROJECT_NETWORK:-easypanel-sell-autonomia}"
global_network="${EASYPANEL_GLOBAL_NETWORK:-easypanel}"
services=(
  sell-autonomia_postgres
  sell-autonomia_redis
  sell-autonomia_storage
)
declare -A cpu_limits=(
  [sell-autonomia_postgres]='1000000000'
  [sell-autonomia_redis]='500000000'
  [sell-autonomia_storage]='750000000'
)
declare -A cpu_reservations=(
  [sell-autonomia_postgres]='100000000'
  [sell-autonomia_redis]='50000000'
  [sell-autonomia_storage]='100000000'
)
declare -A memory_limits=(
  [sell-autonomia_postgres]='1073741824'
  [sell-autonomia_redis]='536870912'
  [sell-autonomia_storage]='1073741824'
)
declare -A memory_reservations=(
  [sell-autonomia_postgres]='268435456'
  [sell-autonomia_redis]='134217728'
  [sell-autonomia_storage]='268435456'
)
declare -A health_commands=(
  [sell-autonomia_postgres]='pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
  [sell-autonomia_redis]='redis-cli -a "$REDIS_PASSWORD" ping 2>/dev/null | grep -qx PONG'
  [sell-autonomia_storage]='curl -fsS http://127.0.0.1:9000/minio/health/ready >/dev/null'
)

for command in docker; do
  command -v "$command" >/dev/null || { echo "missing command: $command" >&2; exit 1; }
done

project_network_id="$(docker network inspect "$project_network" --format '{{.Id}}')"
global_network_id="$(docker network inspect "$global_network" --format '{{.Id}}')"

for service in "${services[@]}"; do
  docker service inspect "$service" >/dev/null

  update_state="$(docker service inspect "$service" --format '{{if .UpdateStatus}}{{.UpdateStatus.State}}{{end}}')"
  [[ -z "$update_state" || "$update_state" == "completed" ]] || {
    echo "$service update state=$update_state" >&2
    exit 1
  }

  desired_replicas="$(docker service inspect "$service" --format '{{.Spec.Mode.Replicated.Replicas}}')"
  [[ "$desired_replicas" == "1" ]] || { echo "$service desired replicas=$desired_replicas" >&2; exit 1; }

  replicas="$(docker service ls --filter "name=$service" --format '{{.Replicas}}')"
  [[ "$replicas" == "1/1" ]] || { echo "$service replicas=$replicas" >&2; exit 1; }

  ports="$(docker service inspect "$service" --format '{{json .Endpoint.Spec.Ports}}')"
  [[ "$ports" == "null" || "$ports" == "[]" ]] || { echo "$service has published ports" >&2; exit 1; }

  mapfile -t networks < <(docker service inspect "$service" --format '{{range .Spec.TaskTemplate.Networks}}{{println .Target}}{{end}}' | sed '/^$/d')
  [[ " ${networks[*]} " == *" $project_network_id "* ]] || { echo "$service missing project network" >&2; exit 1; }
  [[ " ${networks[*]} " != *" $global_network_id "* ]] || { echo "$service attached to global EasyPanel network" >&2; exit 1; }
  [[ "${#networks[@]}" -eq 1 ]] || { echo "$service has unexpected networks: ${networks[*]}" >&2; exit 1; }

  cpu_limit="$(docker service inspect "$service" --format '{{.Spec.TaskTemplate.Resources.Limits.NanoCPUs}}')"
  cpu_reservation="$(docker service inspect "$service" --format '{{.Spec.TaskTemplate.Resources.Reservations.NanoCPUs}}')"
  memory_limit="$(docker service inspect "$service" --format '{{.Spec.TaskTemplate.Resources.Limits.MemoryBytes}}')"
  memory_reservation="$(docker service inspect "$service" --format '{{.Spec.TaskTemplate.Resources.Reservations.MemoryBytes}}')"
  [[ "$cpu_limit" == "${cpu_limits[$service]}" ]] || { echo "$service CPU limit=$cpu_limit" >&2; exit 1; }
  [[ "$cpu_reservation" == "${cpu_reservations[$service]}" ]] || { echo "$service CPU reservation=$cpu_reservation" >&2; exit 1; }
  [[ "$memory_limit" == "${memory_limits[$service]}" ]] || { echo "$service memory limit=$memory_limit" >&2; exit 1; }
  [[ "$memory_reservation" == "${memory_reservations[$service]}" ]] || { echo "$service memory reservation=$memory_reservation" >&2; exit 1; }

  healthcheck="$(docker service inspect "$service" --format '{{json .Spec.TaskTemplate.ContainerSpec.Healthcheck}}')"
  [[ "$healthcheck" != "null" ]] || { echo "$service missing healthcheck" >&2; exit 1; }
  health_test_length="$(docker service inspect "$service" --format '{{len .Spec.TaskTemplate.ContainerSpec.Healthcheck.Test}}')"
  health_test_mode="$(docker service inspect "$service" --format '{{index .Spec.TaskTemplate.ContainerSpec.Healthcheck.Test 0}}')"
  health_test_command="$(docker service inspect "$service" --format '{{index .Spec.TaskTemplate.ContainerSpec.Healthcheck.Test 1}}')"
  health_interval="$(docker service inspect "$service" --format '{{.Spec.TaskTemplate.ContainerSpec.Healthcheck.Interval}}')"
  health_timeout="$(docker service inspect "$service" --format '{{.Spec.TaskTemplate.ContainerSpec.Healthcheck.Timeout}}')"
  health_retries="$(docker service inspect "$service" --format '{{.Spec.TaskTemplate.ContainerSpec.Healthcheck.Retries}}')"
  health_start_period="$(docker service inspect "$service" --format '{{.Spec.TaskTemplate.ContainerSpec.Healthcheck.StartPeriod}}')"
  [[ "$health_test_length" == "2" && "$health_test_mode" == "CMD-SHELL" ]] || { echo "$service invalid healthcheck mode" >&2; exit 1; }
  [[ "$health_test_command" == "${health_commands[$service]}" ]] || { echo "$service healthcheck command drift" >&2; exit 1; }
  [[ "$health_interval" == "15s" ]] || { echo "$service healthcheck interval=$health_interval" >&2; exit 1; }
  [[ "$health_timeout" == "5s" ]] || { echo "$service healthcheck timeout=$health_timeout" >&2; exit 1; }
  [[ "$health_retries" == "5" ]] || { echo "$service healthcheck retries=$health_retries" >&2; exit 1; }
  [[ "$health_start_period" == "20s" ]] || { echo "$service healthcheck start period=$health_start_period" >&2; exit 1; }

  mapfile -t running_tasks < <(docker service ps "$service" --filter desired-state=running --no-trunc --format '{{.ID}}')
  [[ "${#running_tasks[@]}" -eq 1 ]] || { echo "$service running tasks=${#running_tasks[@]}" >&2; exit 1; }
  container_id="$(docker ps --filter "label=com.docker.swarm.task.id=${running_tasks[0]}" --format '{{.ID}}' | head -1)"
  [[ -n "$container_id" ]] || { echo "$service has no running container" >&2; exit 1; }
  health="$(docker inspect "$container_id" --format '{{.State.Health.Status}}')"
  [[ "$health" == "healthy" ]] || { echo "$service health=$health" >&2; exit 1; }

  echo "$service isolation=ok health=healthy"
done

echo "sell-autonomia isolation guard=ok"
