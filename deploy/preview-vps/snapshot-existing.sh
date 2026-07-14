#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /opt/autonomia-sell-preview/issue-66/SHA/snapshots/{before|after}" >&2
  exit 2
fi

output="$1"
[[ "$output" =~ ^/opt/autonomia-sell-preview/issue-66/[0-9a-f]{12}/snapshots/(before|after)$ ]] || {
  echo "Snapshot path is outside the exact Issue #66 run root" >&2
  exit 2
}
run_root="$(dirname "$(dirname "$output")")"
config_dir="$run_root/config"
# shellcheck source=docker-local-guard.sh
source "$config_dir/docker-local-guard.sh"
pin_local_preview_docker
[[ ! -e "$output" && ! -L "$output" ]] || { echo "Refusing to overwrite $output" >&2; exit 2; }
install -d -o root -g root -m 0700 "$output"

docker service ls --format '{{.ID}}\t{{.Name}}\t{{.Image}}\t{{.Mode}}\t{{.Replicas}}' \
  | sort >"$output/swarm-services.tsv"
docker node ls --format '{{.ID}}\t{{.Hostname}}\t{{.Status}}\t{{.Availability}}\t{{.ManagerStatus}}' \
  | sort >"$output/swarm-nodes.tsv"
docker ps -aq | xargs -r docker inspect \
  --format '{{.Id}}\t{{.Name}}\t{{.Config.Image}}\t{{.State.Status}}\t{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}\t{{.RestartCount}}' \
  | sed 's#\t/#\t#' | sort >"$output/containers.tsv"
docker network ls --format '{{.ID}}\t{{.Name}}\t{{.Driver}}\t{{.Scope}}' | sort >"$output/networks.tsv"
docker volume ls --format '{{.Name}}\t{{.Driver}}' | sort >"$output/volumes.tsv"
docker image ls --no-trunc --format '{{.ID}}\t{{.Repository}}\t{{.Tag}}' | sort >"$output/images.tsv"
ss -ltnH | awk '{print $4}' | sort >"$output/listeners.tsv"
docker info --format 'swarm={{.Swarm.LocalNodeState}} root={{.DockerRootDir}}' >"$output/docker-info.txt"
docker_root="$(docker info --format '{{.DockerRootDir}}')"
df --output=avail -k "$docker_root" | tail -1 | tr -d ' ' >"$output/docker-disk-available-kb.txt"
awk '/^MemAvailable:/ {print $2}' /proc/meminfo >"$output/memory-available-kb.txt"

echo "Read-only VPS snapshot written to $output."
