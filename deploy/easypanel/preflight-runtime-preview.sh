#!/usr/bin/env bash
set -euo pipefail
case $- in *x*) set +x ;; esac

[[ $# -eq 3 ]] || { echo "Usage: $0 RUN_ROOT ENV_FILE APPSELL_SOURCE_SHA" >&2; exit 2; }
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-preview-common.sh
source "$here/runtime-preview-common.sh"
runtime_preview_require_root
runtime_preview_require_commands curl docker jq sha256sum realpath ss stat cmp
runtime_preview_assert_run_root "$1" "$3"
runtime_preview_assert_env_file "$2"
source_sha="$3"
config_dir="$RUNTIME_PREVIEW_RUN_ROOT/config"
compose_file="$config_dir/easypanel/compose.runtime-preview.yaml"
runtime_preview_assert_protected_directory "$config_dir"
runtime_preview_assert_protected_directory "$config_dir/easypanel"
runtime_preview_assert_protected_file "$compose_file"
runtime_preview_assert_protected_file "$config_dir/SHA256SUMS"
runtime_preview_assert_protected_file "$config_dir/easypanel/verify-sell-isolation.sh"
(cd "$config_dir" && sha256sum --check SHA256SUMS) >/dev/null
[[ "$(runtime_preview_env_value APPSELL_SOURCE_SHA)" == "$source_sha" ]] || { echo "env source SHA mismatch" >&2; exit 2; }

for record in \
  "APPSELL_SELL_IMAGE|autonomia-sell-preview-sell:$source_sha" \
  "APPSELL_DOMAIN_IMAGE|autonomia-sell-preview-domain:$source_sha" \
  "APPSELL_ALPINE_IMAGE|autonomia-sell-preview-base-alpine:$source_sha"; do
  IFS='|' read -r key expected <<<"$record"
  image="$(runtime_preview_env_value "$key")"
  [[ "$image" == "$expected" ]] || { echo "unexpected image tag for $key" >&2; exit 2; }
  docker image inspect "$image" >/dev/null
  [[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" == "$source_sha" ]] || {
    echo "image revision mismatch for $key" >&2
    exit 2
  }
done

[[ "$(docker info --format '{{.Swarm.LocalNodeState}}')" == "active" ]] || { echo "expected active VPS Swarm" >&2; exit 2; }
docker network inspect "$RUNTIME_PREVIEW_NETWORK" >/dev/null
"$config_dir/easypanel/verify-sell-isolation.sh" >/dev/null
listeners="$(ss -ltnH)" || { echo "could not inventory listening ports" >&2; exit 2; }
if awk '{print $4}' <<<"$listeners" | grep -Eq '(^|:)13200$'; then
  echo "loopback preview port 13200 is already in use" >&2
  exit 2
fi
runtime_containers="$(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT")" || {
  echo "could not inventory runtime-preview containers" >&2
  exit 2
}
if [[ -n "$runtime_containers" ]]; then
  echo "runtime-preview Compose containers already exist" >&2
  exit 2
fi

unset COMPOSE_PROJECT_NAME COMPOSE_FILE COMPOSE_PROFILES COMPOSE_ENV_FILES
docker compose --project-name "$RUNTIME_PREVIEW_COMPOSE_PROJECT" \
  --env-file "$RUNTIME_PREVIEW_ENV_FILE" -f "$compose_file" config --quiet
resolved="$(docker compose --project-name "$RUNTIME_PREVIEW_COMPOSE_PROJECT" \
  --env-file "$RUNTIME_PREVIEW_ENV_FILE" -f "$compose_file" config --format json | jq -r '.name')"
[[ "$resolved" == "$RUNTIME_PREVIEW_COMPOSE_PROJECT" ]] || { echo "Compose project name drift" >&2; exit 2; }

runtime_preview_api_login
service_state="$(runtime_preview_service_state)"
if [[ "$service_state" == "present" ]]; then
  runtime_preview_verify_attestation 'stopped|provisioning|configured'
  attestation_state="$(runtime_preview_attestation_value state)"
  if [[ "$attestation_state" == "provisioning" ]]; then
    runtime_preview_verify_live_record no
  else
    runtime_preview_verify_live_record yes
  fi
elif [[ "$service_state" != "absent" ]]; then
  exit 2
fi
snapshot="$RUNTIME_PREVIEW_RUN_ROOT/protected-services.before.tsv"
if [[ -e "$snapshot" ]]; then
  runtime_preview_verify_service_snapshot "$snapshot"
else
  runtime_preview_snapshot_services "$snapshot"
fi
echo "runtime-preview preflight passed; no EasyPanel or Docker resource was created"
