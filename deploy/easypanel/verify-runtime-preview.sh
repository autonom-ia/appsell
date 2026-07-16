#!/usr/bin/env bash
set -euo pipefail
case $- in *x*) set +x ;; esac

[[ $# -eq 3 ]] || { echo "Usage: $0 RUN_ROOT ENV_FILE APPSELL_SOURCE_SHA" >&2; exit 2; }
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-preview-common.sh
source "$here/runtime-preview-common.sh"
runtime_preview_require_root
runtime_preview_require_commands curl docker jq realpath stat sha256sum
runtime_preview_assert_run_root "$1" "$3"
runtime_preview_assert_env_file "$2"
source_sha="$3"
runtime_preview_api_login
[[ "$(runtime_preview_service_state)" == "present" ]] || { echo "EasyPanel runtime-preview is absent" >&2; exit 1; }
runtime_preview_verify_attestation 'configured|active'
runtime_preview_verify_live_record yes

ready=0
for _attempt in $(seq 1 60); do
  mapfile -t ids < <(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT")
  if [[ ${#ids[@]} -eq 3 ]]; then
    domain_state="$(docker inspect --format '{{.State.Status}}:{{if .State.Health}}{{.State.Health.Status}}{{end}}:{{.RestartCount}}' \
      "$(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" --filter 'label=com.docker.compose.service=domain-api')" 2>/dev/null || true)"
    web_state="$(docker inspect --format '{{.State.Status}}:{{if .State.Health}}{{.State.Health.Status}}{{end}}:{{.RestartCount}}' \
      "$(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" --filter 'label=com.docker.compose.service=sell-web')" 2>/dev/null || true)"
    bootstrap_state="$(docker inspect --format '{{.State.Status}}:{{.State.ExitCode}}' \
      "$(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" --filter 'label=com.docker.compose.service=domain-bootstrap')" 2>/dev/null || true)"
    if [[ "$domain_state" == "running:healthy:0" && "$web_state" == "running:healthy:0" && "$bootstrap_state" == "exited:0" ]]; then
      ready=1
      break
    fi
  fi
  sleep 3
done
[[ "$ready" == "1" ]] || { echo "runtime-preview did not become healthy within 180 seconds" >&2; exit 1; }
declare -A seen=()
for id in "${ids[@]}"; do
  [[ "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$id")" == "$RUNTIME_PREVIEW_COMPOSE_PROJECT" ]] || exit 1
  [[ "$(docker inspect --format '{{index .Config.Labels "com.autonomia.project"}}' "$id")" == "$RUNTIME_PREVIEW_PROJECT" ]] || exit 1
  [[ "$(docker inspect --format '{{index .Config.Labels "com.autonomia.issue"}}' "$id")" == "77" ]] || exit 1
  [[ "$(docker inspect --format '{{index .Config.Labels "com.autonomia.source-sha"}}' "$id")" == "$source_sha" ]] || exit 1
  service="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$id")"
  case "$service" in domain-bootstrap|domain-api|sell-web) ;; *) echo "unexpected runtime service: $service" >&2; exit 1 ;; esac
  [[ -z "${seen[$service]:-}" ]] || exit 1
  seen[$service]=1
  image="$(docker inspect --format '{{.Config.Image}}' "$id")"
  case "$service" in
    domain-bootstrap|domain-api) [[ "$image" == "autonomia-sell-preview-domain:$source_sha" ]] ;;
    sell-web) [[ "$image" == "autonomia-sell-preview-sell:$source_sha" ]] ;;
  esac || { echo "unexpected image for $service" >&2; exit 1; }
  if [[ "$service" == "domain-bootstrap" ]]; then
    [[ "$(docker inspect --format '{{.State.Status}}:{{.State.ExitCode}}' "$id")" == "exited:0" ]] || exit 1
  else
    [[ "$(docker inspect --format '{{.State.Status}}:{{.State.Health.Status}}:{{.RestartCount}}' "$id")" == "running:healthy:0" ]] || exit 1
  fi
done
[[ ${#seen[@]} -eq 3 ]] || exit 1

for service in domain-bootstrap domain-api sell-web; do
  mapfile -t service_ids < <(docker ps -aq \
    --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" \
    --filter "label=com.docker.compose.service=$service")
  [[ ${#service_ids[@]} -eq 1 ]] || {
    echo "$service does not have exactly one runtime-preview container" >&2
    exit 1
  }
  inspect_json="$(docker inspect "${service_ids[0]}")"
  runtime_preview_inspect_has_exact_network "$inspect_json" "$RUNTIME_PREVIEW_NETWORK" || {
    echo "$service escaped the exclusive sell network" >&2
    exit 1
  }
done
sell_id="$(docker ps -q --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" --filter 'label=com.docker.compose.service=sell-web')"
ports="$(docker inspect --format '{{json .NetworkSettings.Ports}}' "$sell_id")"
jq -e '.["13200/tcp"] | length == 1 and .[0].HostIp == "127.0.0.1" and .[0].HostPort == "13200"' <<<"$ports" >/dev/null
curl -fsS --connect-timeout 3 --max-time 15 http://127.0.0.1:13200/ >/dev/null
"$RUNTIME_PREVIEW_RUN_ROOT/config/easypanel/verify-sell-isolation.sh" >/dev/null
runtime_preview_verify_service_snapshot "$RUNTIME_PREVIEW_RUN_ROOT/protected-services.before.tsv"

if [[ "${RUN_RUNTIME_PREVIEW_E2E:-0}" == "1" ]]; then
  compose_file="$RUNTIME_PREVIEW_RUN_ROOT/config/easypanel/compose.runtime-preview.yaml"
  unset COMPOSE_PROJECT_NAME COMPOSE_FILE COMPOSE_PROFILES COMPOSE_ENV_FILES
  compose=(docker compose --project-name "$RUNTIME_PREVIEW_COMPOSE_PROJECT" --env-file "$RUNTIME_PREVIEW_ENV_FILE" -f "$compose_file")
  "${compose[@]}" --profile e2e run --pull never --rm evidence-init >/dev/null
  "${compose[@]}" --profile e2e run --pull never --rm e2e
  "${compose[@]}" --profile e2e run --pull never --rm --no-deps --entrypoint sh evidence-init -ec \
    'test -s /evidence/current/admin-dashboard.png && test -s /evidence/current/buyer-player.png && test -s /evidence/current/superadmin-inspection.png'
fi
echo "runtime-preview health, image SHA, loopback port, exclusive network and protected-service invariants passed"
