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
# shellcheck source=docker-local-guard.sh
source "$config_dir/docker-local-guard.sh"
pin_local_preview_docker
# shellcheck source=load-preview-env.sh
source "$config_dir/load-preview-env.sh"
load_preview_env "$env_file"
unset COMPOSE_PROJECT_NAME COMPOSE_FILE COMPOSE_PROFILES COMPOSE_ENV_FILES
compose=(docker compose --project-name "$PREVIEW_PROJECT_NAME" --env-file "$env_file" -f "$compose_file")

curl --fail --silent --show-error http://127.0.0.1:13200/ >/dev/null
curl --fail --silent --show-error http://127.0.0.1:13217/login/ >/dev/null
"${compose[@]}" ps

network="${PREVIEW_PROJECT_NAME}_preview"
[[ "$(docker network inspect --format '{{index .Options "com.docker.network.bridge.gateway_mode_ipv4"}}' "$network")" == "isolated" ]]
netns_id="$("${compose[@]}" ps -q netns)"
ports="$(docker inspect --format '{{json .NetworkSettings.Ports}}' "$netns_id")"
jq -e '.["13200/tcp"] | length == 1 and .[0].HostIp == "127.0.0.1" and .[0].HostPort == "13200"' <<<"$ports" >/dev/null
jq -e '.["13217/tcp"] | length == 1 and .[0].HostIp == "127.0.0.1" and .[0].HostPort == "13217"' <<<"$ports" >/dev/null

for service in netns domain-postgres identity-postgres redis foundation domain-api auth sell; do
  id="$("${compose[@]}" ps -q "$service")"
  docker inspect --format '{{if and (gt .HostConfig.Memory 0) (gt .HostConfig.NanoCpus 0) (gt .HostConfig.PidsLimit 0)}}ok{{else}}missing-runtime-limit{{end}}' "$id" | grep -qx ok
  docker inspect --format '{{.HostConfig.LogConfig.Type}} {{index .HostConfig.LogConfig.Config "max-size"}} {{index .HostConfig.LogConfig.Config "max-file"}}' "$id" | grep -qx 'json-file 10m 3'
  if docker inspect --format '{{json .Config.Env}}' "$id" | grep -Eq 'FINANCIAL|NEURO|GITHUB_TOKEN|PACKAGES_READ_TOKEN'; then
    echo "Forbidden external integration environment reached $service" >&2
    exit 1
  fi
done

for record in \
  "${APPSELL_SELL_IMAGE}|${APPSELL_SOURCE_SHA}|${APPSELL_SELL_IMAGE_ID}" \
  "${APPSELL_DOMAIN_IMAGE}|${APPSELL_SOURCE_SHA}|${APPSELL_DOMAIN_IMAGE_ID}" \
  "${APPSELL_ALPINE_IMAGE}|${APPSELL_SOURCE_SHA}|${APPSELL_ALPINE_IMAGE_ID}" \
  "${APPSELL_POSTGRES_IMAGE}|${APPSELL_SOURCE_SHA}|${APPSELL_POSTGRES_IMAGE_ID}" \
  "${APPSELL_REDIS_IMAGE}|${APPSELL_SOURCE_SHA}|${APPSELL_REDIS_IMAGE_ID}" \
  "${IDENTITY_AUTH_IMAGE}|${IDENTITY_AUTH_SOURCE_SHA}|${IDENTITY_AUTH_IMAGE_ID}" \
  "${IDENTITY_FOUNDATION_IMAGE}|${IDENTITY_FOUNDATION_SOURCE_SHA}|${IDENTITY_FOUNDATION_IMAGE_ID}"; do
  IFS='|' read -r image sha image_id <<<"$record"
  [[ "$(docker image inspect --format '{{.Id}}' "$image")" == "$image_id" ]] || exit 1
  [[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" == "$sha" ]] || {
    echo "Image revision label does not match the approved SHA" >&2
    exit 1
  }
done
unit="autonomia-sell-preview-66-${APPSELL_SOURCE_SHA:0:12}-expiry.timer"
[[ "$(systemctl is-active "$unit")" == "active" ]] || { echo "Expiry timer is not active" >&2; exit 1; }

"${compose[@]}" --profile e2e run --pull never --rm e2e
"${compose[@]}" --profile e2e run --pull never --rm --no-deps --entrypoint sh evidence-init -ec \
  'test -s /evidence/current/admin-dashboard.png && test -s /evidence/current/buyer-library.png && test -s /evidence/current/buyer-first-lesson.png'

echo "Preview health, digests, limits, isolated network and real E2E passed."
