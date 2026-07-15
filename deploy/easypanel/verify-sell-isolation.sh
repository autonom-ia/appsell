#!/usr/bin/env bash
set -euo pipefail

project_network="${SELL_PROJECT_NETWORK:-easypanel-sell-autonomia}"
global_network="${EASYPANEL_GLOBAL_NETWORK:-easypanel}"
services=(
  sell-autonomia_postgres
  sell-autonomia_redis
  sell-autonomia_storage
)

for command in docker grep; do
  command -v "$command" >/dev/null || { echo "missing command: $command" >&2; exit 1; }
done

project_network_id="$(docker network inspect "$project_network" --format '{{.Id}}')"
global_network_id="$(docker network inspect "$global_network" --format '{{.Id}}')"

for service in "${services[@]}"; do
  docker service inspect "$service" >/dev/null

  replicas="$(docker service ls --filter "name=$service" --format '{{.Replicas}}')"
  [[ "$replicas" == "1/1" ]] || { echo "$service replicas=$replicas" >&2; exit 1; }

  ports="$(docker service inspect "$service" --format '{{json .Endpoint.Spec.Ports}}')"
  [[ "$ports" == "null" || "$ports" == "[]" ]] || { echo "$service has published ports" >&2; exit 1; }

  mapfile -t networks < <(docker service inspect "$service" --format '{{range .Spec.TaskTemplate.Networks}}{{println .Target}}{{end}}' | sed '/^$/d')
  [[ " ${networks[*]} " == *" $project_network_id "* ]] || { echo "$service missing project network" >&2; exit 1; }
  [[ " ${networks[*]} " != *" $global_network_id "* ]] || { echo "$service attached to global EasyPanel network" >&2; exit 1; }
  [[ "${#networks[@]}" -eq 1 ]] || { echo "$service has unexpected networks: ${networks[*]}" >&2; exit 1; }

  resources="$(docker service inspect "$service" --format '{{json .Spec.TaskTemplate.Resources}}')"
  grep -q 'MemoryBytes' <<<"$resources" || { echo "$service missing memory limits" >&2; exit 1; }
  grep -q 'NanoCPUs' <<<"$resources" || { echo "$service missing CPU limits" >&2; exit 1; }

  healthcheck="$(docker service inspect "$service" --format '{{json .Spec.TaskTemplate.ContainerSpec.Healthcheck}}')"
  [[ "$healthcheck" != "null" ]] || { echo "$service missing healthcheck" >&2; exit 1; }

  container_id="$(docker ps --filter "label=com.docker.swarm.service.name=$service" --format '{{.ID}}' | head -1)"
  [[ -n "$container_id" ]] || { echo "$service has no running container" >&2; exit 1; }
  health="$(docker inspect "$container_id" --format '{{.State.Health.Status}}')"
  [[ "$health" == "healthy" ]] || { echo "$service health=$health" >&2; exit 1; }

  echo "$service isolation=ok health=healthy"
done

echo "sell-autonomia isolation guard=ok"
