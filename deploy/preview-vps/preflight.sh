#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 COMPOSE_FILE ENV_FILE" >&2
  exit 2
fi

compose_file="$(realpath -e "$1")"
env_file="$2"
[[ "$compose_file" =~ ^/opt/autonomia-sell-preview/issue-66/[0-9a-f]{12}/config/compose\.preview\.yaml$ ]] || {
  echo "Compose file is outside the exact Issue #66 config root" >&2
  exit 2
}
config_dir="$(dirname "$compose_file")"
[[ -f "$config_dir/validate-root-tree.sh" && ! -L "$config_dir/validate-root-tree.sh" ]] || exit 2
[[ "$(stat -c '%u:%g' "$config_dir/validate-root-tree.sh")" == "0:0" ]] || exit 2
[[ "$(stat -c '%h' "$config_dir/validate-root-tree.sh")" == "1" ]] || exit 2
[[ ! -w "$config_dir/validate-root-tree.sh" || "$(id -u)" == "0" ]] || exit 2
bash "$config_dir/validate-root-tree.sh" "$config_dir"
# shellcheck source=load-preview-env.sh
source "$config_dir/load-preview-env.sh"
load_preview_env "$env_file"

[[ "$PREVIEW_PROJECT_NAME" == "autonomia_sell_preview_66_${APPSELL_SOURCE_SHA:0:12}" ]] || {
  echo "Unexpected preview project name" >&2
  exit 2
}
[[ "$IDENTITY_DATABASE_NAME" =~ ^identity_oauth_local_[0-9a-f]{32}$ ]] || {
  echo "Invalid Identity database sentinel" >&2
  exit 2
}
for sha in "$APPSELL_SOURCE_SHA" "$IDENTITY_AUTH_SOURCE_SHA" "$IDENTITY_FOUNDATION_SOURCE_SHA"; do
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 2
done
expires_epoch="$(date -u -d "$PREVIEW_EXPIRES_AT" +%s)" || exit 2
now_epoch="$(date -u +%s)"
(( expires_epoch > now_epoch + 300 && expires_epoch <= now_epoch + 72 * 3600 )) || {
  echo "Preview expiry must be between 5 minutes and 72 hours from now" >&2
  exit 2
}

records=(
  "$APPSELL_SELL_IMAGE|autonomia-sell-preview-sell:$APPSELL_SOURCE_SHA|$APPSELL_SELL_IMAGE_ID|$APPSELL_SOURCE_SHA"
  "$APPSELL_DOMAIN_IMAGE|autonomia-sell-preview-domain:$APPSELL_SOURCE_SHA|$APPSELL_DOMAIN_IMAGE_ID|$APPSELL_SOURCE_SHA"
  "$IDENTITY_AUTH_IMAGE|autonomia-identity-auth-preview:$IDENTITY_AUTH_SOURCE_SHA|$IDENTITY_AUTH_IMAGE_ID|$IDENTITY_AUTH_SOURCE_SHA"
  "$IDENTITY_FOUNDATION_IMAGE|autonomia-identity-foundation-preview:$IDENTITY_FOUNDATION_SOURCE_SHA|$IDENTITY_FOUNDATION_IMAGE_ID|$IDENTITY_FOUNDATION_SOURCE_SHA"
)
for record in "${records[@]}"; do
  IFS='|' read -r image expected expected_id sha <<<"$record"
  [[ "$image" == "$expected" && "$expected_id" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 2
  [[ "$(docker image inspect --format '{{.Id}}' "$image")" == "$expected_id" ]] || exit 2
  [[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" == "$sha" ]] || exit 2
done

server_version="$(docker version --format '{{.Server.Version}}')"
minimum="$(printf '%s\n%s\n' 28.0.0 "$server_version" | sort -V | head -1)"
[[ "$minimum" == "28.0.0" ]] || { echo "Docker Engine >=28 is required" >&2; exit 2; }
[[ "$(docker info --format '{{.Swarm.LocalNodeState}}')" == "active" ]] || {
  echo "Expected the known VPS Swarm to remain active" >&2
  exit 2
}
docker compose version >/dev/null
command -v systemctl >/dev/null
if [[ -r /etc/docker/daemon.json ]] && jq -e '.["allow-direct-routing"] == true' /etc/docker/daemon.json >/dev/null; then
  echo "Docker allow-direct-routing must not be enabled" >&2
  exit 2
fi

for service in netns domain-postgres identity-postgres redis foundation domain-bootstrap domain-api auth sell; do
  name="${PREVIEW_PROJECT_NAME}-${service}-1"
  if docker ps -a --format '{{.Names}}' | grep -Fxq "$name"; then
    echo "Container name collision: $name" >&2
    exit 2
  fi
done
for network in "${PREVIEW_PROJECT_NAME}_preview"; do
  if docker network ls --format '{{.Name}}' | grep -Fxq "$network"; then
    echo "Network name collision: $network" >&2
    exit 2
  fi
done
for volume in domain_pg_data identity_pg_data e2e_evidence; do
  name="${PREVIEW_PROJECT_NAME}_${volume}"
  if docker volume ls --format '{{.Name}}' | grep -Fxq "$name"; then
    echo "Volume name collision: $name" >&2
    exit 2
  fi
done
if ss -ltnH | awk '{print $4}' | grep -Eq '(^|:)(13200|13217)$'; then
  echo "A preview host port is already in use" >&2
  exit 2
fi

docker_root="$(docker info --format '{{.DockerRootDir}}')"
available_kb="$(df --output=avail "$docker_root" | tail -1 | tr -d ' ')"
(( available_kb >= 30 * 1024 * 1024 )) || { echo "Less than 30 GiB is available in DockerRootDir" >&2; exit 2; }
available_mem_kb="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
(( available_mem_kb >= 12 * 1024 * 1024 )) || { echo "Less than 12 GiB of host memory is available" >&2; exit 2; }
cpu_count="$(getconf _NPROCESSORS_ONLN)"
(( cpu_count >= 8 )) || { echo "Fewer than 8 logical CPUs are available" >&2; exit 2; }
load_milli="$(awk '{printf "%d", $1 * 1000}' /proc/loadavg)"
(( load_milli <= cpu_count * 500 )) || { echo "One-minute load exceeds 50% of logical CPU capacity" >&2; exit 2; }
unit="autonomia-sell-preview-66-${APPSELL_SOURCE_SHA:0:12}-expiry.timer"
[[ "$(systemctl show -p LoadState --value "$unit" 2>/dev/null || true)" != "loaded" ]] || { echo "Expiry timer collision" >&2; exit 2; }
[[ ! -e "/etc/systemd/system/$unit" && ! -e "/etc/systemd/system/${unit%.timer}.service" ]] || { echo "Expiry unit file collision" >&2; exit 2; }

docker compose --env-file "$env_file" -f "$compose_file" config --quiet
echo "Preview preflight passed without creating resources."
