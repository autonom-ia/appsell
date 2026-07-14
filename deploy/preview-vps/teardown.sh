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
bash "$config_dir/validate-root-tree.sh" "$config_dir"
# shellcheck source=load-preview-env.sh
source "$config_dir/load-preview-env.sh"
load_preview_env "$env_file"

[[ "$PREVIEW_PROJECT_NAME" == "autonomia_sell_preview_66_${APPSELL_SOURCE_SHA:0:12}" ]] || exit 2

container_ids="$(docker ps -aq --filter "label=com.docker.compose.project=$PREVIEW_PROJECT_NAME")"
if [[ -n "$container_ids" ]]; then
  while read -r id; do
    [[ "$(docker inspect --format '{{index .Config.Labels "com.autonomia.preview"}}' "$id")" == "true" ]] || exit 2
    [[ "$(docker inspect --format '{{index .Config.Labels "com.autonomia.issue"}}' "$id")" == "66" ]] || exit 2
    [[ "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$id")" == "$PREVIEW_PROJECT_NAME" ]] || exit 2
  done <<<"$container_ids"
fi

network="${PREVIEW_PROJECT_NAME}_preview"
if docker network inspect "$network" >/dev/null 2>&1; then
  [[ "$(docker network inspect --format '{{index .Labels "com.autonomia.preview"}}' "$network")" == "true" ]] || exit 2
  [[ "$(docker network inspect --format '{{index .Labels "com.autonomia.issue"}}' "$network")" == "66" ]] || exit 2
  [[ "$(docker network inspect --format '{{index .Labels "com.docker.compose.project"}}' "$network")" == "$PREVIEW_PROJECT_NAME" ]] || exit 2
fi
for suffix in domain_pg_data identity_pg_data e2e_evidence; do
  volume="${PREVIEW_PROJECT_NAME}_${suffix}"
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    [[ "$(docker volume inspect --format '{{index .Labels "com.autonomia.preview"}}' "$volume")" == "true" ]] || exit 2
    [[ "$(docker volume inspect --format '{{index .Labels "com.autonomia.issue"}}' "$volume")" == "66" ]] || exit 2
    [[ "$(docker volume inspect --format '{{index .Labels "com.docker.compose.project"}}' "$volume")" == "$PREVIEW_PROJECT_NAME" ]] || exit 2
  fi
done

down_ok=0
for attempt in 1 2 3; do
  if docker compose --env-file "$env_file" -f "$compose_file" down --volumes --remove-orphans; then
    down_ok=1
    break
  fi
  sleep 10
done
[[ "$down_ok" == "1" ]] || { echo "Compose teardown failed after three attempts" >&2; exit 1; }
for record in \
  "$APPSELL_SELL_IMAGE|$APPSELL_SOURCE_SHA|$APPSELL_SELL_IMAGE_ID" \
  "$APPSELL_DOMAIN_IMAGE|$APPSELL_SOURCE_SHA|$APPSELL_DOMAIN_IMAGE_ID" \
  "$IDENTITY_AUTH_IMAGE|$IDENTITY_AUTH_SOURCE_SHA|$IDENTITY_AUTH_IMAGE_ID" \
  "$IDENTITY_FOUNDATION_IMAGE|$IDENTITY_FOUNDATION_SOURCE_SHA|$IDENTITY_FOUNDATION_IMAGE_ID"; do
  IFS='|' read -r image sha image_id <<<"$record"
  if docker image inspect "$image" >/dev/null 2>&1; then
    [[ "$(docker image inspect --format '{{.Id}}' "$image")" == "$image_id" ]] || exit 2
    [[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" == "$sha" ]] || exit 2
    docker image rm "$image" >/dev/null
  fi
done
unit="autonomia-sell-preview-66-${APPSELL_SOURCE_SHA:0:12}-expiry"
systemctl disable --now "$unit.timer" >/dev/null 2>&1 || true
service_file="/etc/systemd/system/$unit.service"
timer_file="/etc/systemd/system/$unit.timer"
for file in "$service_file" "$timer_file"; do
  if [[ -e "$file" ]]; then
    [[ ! -L "$file" && "$(stat -c '%u:%g' "$file")" == "0:0" ]] || exit 2
    rm -f -- "$file"
  fi
done
systemctl daemon-reload
run_root="$(dirname "$config_dir")"
[[ "$run_root" =~ ^/opt/autonomia-sell-preview/issue-66/[0-9a-f]{12}$ ]] || exit 2
rm -rf --one-file-system -- "$run_root"
echo "Removed only the exact Issue #66 Compose project $PREVIEW_PROJECT_NAME."
