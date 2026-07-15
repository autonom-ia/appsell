#!/usr/bin/env bash
set -euo pipefail

confirmation="${CONFIRM_SELL_AUTONOMIA_HARDENING:-}"
[[ "$confirmation" == "sell-autonomia-v1" ]] || {
  echo "set CONFIRM_SELL_AUTONOMIA_HARDENING=sell-autonomia-v1" >&2
  exit 2
}

global_network="${EASYPANEL_GLOBAL_NETWORK:-easypanel}"
global_network_id="$(docker network inspect "$global_network" --format '{{.Id}}')"

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

for service in "${services[@]}"; do
  docker service inspect "$service" >/dev/null
  mapfile -t networks < <(docker service inspect "$service" --format '{{range .Spec.TaskTemplate.Networks}}{{println .Target}}{{end}}' | sed '/^$/d')
  network_args=()
  if [[ " ${networks[*]} " == *" $global_network_id "* ]]; then
    network_args=(--network-rm "$global_network")
  fi

  docker service update --detach=true \
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

for _ in $(seq 1 60); do
  ready=true
  for service in "${services[@]}"; do
    replicas="$(docker service ls --filter "name=$service" --format '{{.Replicas}}')"
    container_id="$(docker ps --filter "label=com.docker.swarm.service.name=$service" --format '{{.ID}}' | head -1)"
    health="starting"
    [[ -n "$container_id" ]] && health="$(docker inspect "$container_id" --format '{{.State.Health.Status}}' 2>/dev/null || true)"
    [[ "$replicas" == "1/1" && "$health" == "healthy" ]] || ready=false
  done
  [[ "$ready" == "true" ]] && break
  sleep 2
done

"$(dirname "$0")/verify-sell-isolation.sh"
