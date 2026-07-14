#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "Usage: $0 RUN_ROOT APPSELL_SHA AUTH_SHA FOUNDATION_SHA" >&2
  exit 2
fi
[[ "${AUTONOMIA_PREVIEW_ORCHESTRATED:-}" == "1" ]] || {
  echo "Use activate-preview.sh so partial image loads are tracked and rolled back" >&2
  exit 2
}

run_root="$(realpath -e "$1")"
appsell_sha="$2"
auth_sha="$3"
foundation_sha="$4"
[[ "$run_root" =~ ^/opt/autonomia-sell-preview/issue-66/[0-9a-f]{12}$ ]] || exit 2
for sha in "$appsell_sha" "$auth_sha" "$foundation_sha"; do
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 2
done
[[ "${appsell_sha:0:12}" == "$(basename "$run_root")" ]] || exit 2

artifacts="$run_root/artifacts"
config_dir="$run_root/config"
validator="$run_root/config/validate-root-tree.sh"
[[ -f "$validator" && ! -L "$validator" ]] || exit 2
bash "$validator" "$config_dir"
bash "$validator" "$artifacts"
# shellcheck source=docker-local-guard.sh
source "$config_dir/docker-local-guard.sh"
pin_local_preview_docker

for command_name in docker jq ss openssl timeout findmnt systemctl flock sync; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Missing required VPS command: $command_name" >&2
    exit 2
  }
done
[[ "$(docker version --format '{{.Server.Arch}}')" == "amd64" ]] || {
  echo "Preview artifacts require an amd64 Docker server" >&2
  exit 2
}
docker compose version --short >/dev/null
docker compose up --help | grep -Fq -- '--wait' || {
  echo "Docker Compose must support up --wait" >&2
  exit 2
}
docker compose config --help | grep -Fq -- '--format' || {
  echo "Docker Compose must support config --format" >&2
  exit 2
}
[[ "$(docker info --format '{{.Swarm.LocalNodeState}}')" == "active" ]] || exit 2
if ss -ltnH | awk '{print $4}' | grep -Eq '(^|:)(13200|13217)$'; then
  echo "A preview host port is already in use" >&2
  exit 2
fi

records=(
  "sell-$appsell_sha.tar|autonomia-sell-preview-sell:$appsell_sha|$appsell_sha"
  "domain-$appsell_sha.tar|autonomia-sell-preview-domain:$appsell_sha|$appsell_sha"
  "base-alpine-$appsell_sha.tar|autonomia-sell-preview-base-alpine:$appsell_sha|$appsell_sha"
  "base-postgres-$appsell_sha.tar|autonomia-sell-preview-base-postgres:$appsell_sha|$appsell_sha"
  "base-redis-$appsell_sha.tar|autonomia-sell-preview-base-redis:$appsell_sha|$appsell_sha"
  "auth-$auth_sha.tar|autonomia-identity-auth-preview:$auth_sha|$auth_sha"
  "foundation-$foundation_sha.tar|autonomia-identity-foundation-preview:$foundation_sha|$foundation_sha"
)
total_bytes=0
for record in "${records[@]}"; do
  IFS='|' read -r file image sha <<<"$record"
  [[ -f "$artifacts/$file" ]] || { echo "Missing $file" >&2; exit 2; }
  size_bytes="$(stat -c '%s' "$artifacts/$file")"
  (( size_bytes > 0 && size_bytes <= 8 * 1024 * 1024 * 1024 )) || {
    echo "Docker TAR size is outside the 0..8 GiB gate: $file" >&2
    exit 2
  }
  total_bytes=$((total_bytes + size_bytes))
  if tar -tf "$artifacts/$file" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
    echo "Unsafe path in Docker TAR: $file" >&2
    exit 2
  fi
  manifest="$(tar -xOf "$artifacts/$file" manifest.json)"
  jq -e --arg image "$image" 'length == 1 and .[0].RepoTags == [$image] and (.[] | .Config | type == "string")' <<<"$manifest" >/dev/null || {
    echo "Docker TAR must contain exactly the expected RepoTag: $image" >&2
    exit 2
  }
  config_path="$(jq -r '.[0].Config' <<<"$manifest")"
  [[ "$config_path" =~ ^[0-9a-f]{64}\.json$ ]] || exit 2
  tar -xOf "$artifacts/$file" "$config_path" \
    | jq -e --arg sha "$sha" '.config.Labels["org.opencontainers.image.revision"] == $sha' >/dev/null || {
      echo "Docker TAR config revision mismatch: $image" >&2
      exit 2
    }
  if docker image inspect "$image" >/dev/null 2>&1; then
    echo "Refusing image tag collision: $image" >&2
    exit 2
  fi
done
(( total_bytes <= 20 * 1024 * 1024 * 1024 )) || {
  echo "Docker TAR bundle exceeds the 20 GiB gate" >&2
  exit 2
}
docker_root="$(docker info --format '{{.DockerRootDir}}')"
available_bytes="$(df -B1 --output=avail "$docker_root" | tail -1 | tr -d ' ')"
required_bytes=$((30 * 1024 * 1024 * 1024 + 3 * total_bytes))
(( available_bytes >= required_bytes )) || {
  echo "Insufficient Docker disk headroom before image load" >&2
  exit 2
}
available_mem_kb="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
(( available_mem_kb >= 12 * 1024 * 1024 )) || {
  echo "Less than 12 GiB of host memory is available before image load" >&2
  exit 2
}
cpu_count="$(getconf _NPROCESSORS_ONLN)"
(( cpu_count >= 8 )) || { echo "Fewer than 8 logical CPUs are available" >&2; exit 2; }
load_milli="$(awk '{printf "%d", $1 * 1000}' /proc/loadavg)"
(( load_milli <= cpu_count * 500 )) || {
  echo "One-minute load exceeds 50% before image load" >&2
  exit 2
}

record_file="$run_root/loaded-images.tsv"
[[ ! -e "$record_file" && ! -L "$record_file" ]] || { echo "Loaded-image record collision" >&2; exit 2; }
install -o root -g root -m 0600 /dev/null "$record_file"
pending_image=""
cleanup_on_error() {
  local status=$?
  trap - ERR INT TERM EXIT
  (( status != 0 )) || status=1
  "$run_root/config/remove-loaded-images.sh" "$run_root" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup_on_error ERR INT TERM EXIT

for record in "${records[@]}"; do
  IFS='|' read -r file image sha <<<"$record"
  pending_image="$image"
  printf '%s||%s\n' "$image" "$sha" >>"$record_file"
  sync -f "$record_file"
  docker image load --input "$artifacts/$file" >/dev/null
  image_id="$(docker image inspect --format '{{.Id}}' "$image")"
  [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 1
  [[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" == "$sha" ]] || {
    echo "Loaded image revision mismatch: $image" >&2
    exit 1
  }
  printf '%s|%s|%s\n' "$image" "$image_id" "$sha" >>"$record_file"
  sync -f "$record_file"
  pending_image=""
done
trap - ERR INT TERM EXIT

echo "Loaded seven reviewed CI artifacts without building or pulling on the VPS."
