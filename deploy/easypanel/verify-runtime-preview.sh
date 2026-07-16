#!/usr/bin/env bash
set -euo pipefail
case $- in *x*) set +x ;; esac

[[ $# -eq 3 ]] || { echo "Usage: $0 RUN_ROOT ENV_FILE APPSELL_SOURCE_SHA" >&2; exit 2; }
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-preview-common.sh
source "$here/runtime-preview-common.sh"
runtime_preview_require_root
runtime_preview_require_commands curl dig docker jq openssl realpath sha256sum stat timeout
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
baseline_container_ids="$(printf '%s\n' "${ids[@]}" | LC_ALL=C sort)"
declare -A seen=()
declare -A baseline_lifecycle=()
for id in "${ids[@]}"; do
  [[ "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$id")" == "$RUNTIME_PREVIEW_COMPOSE_PROJECT" ]] || exit 1
  [[ "$(docker inspect --format '{{index .Config.Labels "com.autonomia.project"}}' "$id")" == "$RUNTIME_PREVIEW_PROJECT" ]] || exit 1
  [[ "$(docker inspect --format '{{index .Config.Labels "com.autonomia.issue"}}' "$id")" == "77" ]] || exit 1
  [[ "$(docker inspect --format '{{index .Config.Labels "com.autonomia.source-sha"}}' "$id")" == "$source_sha" ]] || exit 1
  service="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$id")"
  case "$service" in domain-bootstrap|domain-api|sell-web) ;; *) echo "unexpected runtime service: $service" >&2; exit 1 ;; esac
  [[ -z "${seen[$service]:-}" ]] || exit 1
  seen[$service]=1
  baseline_lifecycle[$service]="$(runtime_preview_lifecycle_fingerprint_from_inspect "$(docker inspect "$id")")"
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
runtime_preview_verify_e2e_network() {
  [[ $# -eq 1 ]] || return 2
  local bridge_full_id="$1" network_json
  network_json="$(docker network inspect "$RUNTIME_PREVIEW_E2E_NETWORK")"
  jq -e \
    --arg network "$RUNTIME_PREVIEW_E2E_NETWORK" \
    --arg project "$RUNTIME_PREVIEW_PROJECT" \
    --arg compose_project "$RUNTIME_PREVIEW_COMPOSE_PROJECT" \
    --arg bridge_id "$bridge_full_id" \
    --arg source_sha "$source_sha" '
      type == "array" and length == 1 and
      .[0].Name == $network and
      .[0].Internal == false and
      .[0].Labels["com.docker.compose.project"] == $compose_project and
      .[0].Labels["com.docker.compose.network"] == "sell-public-e2e" and
      .[0].Labels["com.autonomia.project"] == $project and
      .[0].Labels["com.autonomia.issue"] == "77" and
      .[0].Labels["com.autonomia.source-sha"] == $source_sha and
      (.[0].Containers | type == "object" and keys == [$bridge_id])
    ' <<<"$network_json" >/dev/null
}
ports="$(docker inspect --format '{{json .NetworkSettings.Ports}}' "$sell_id")"
jq -e '.["13200/tcp"] | length == 1 and .[0].HostIp == "127.0.0.1" and .[0].HostPort == "13200"' <<<"$ports" >/dev/null
curl -fsS --connect-timeout 3 --max-time 15 http://127.0.0.1:13200/ >/dev/null
"$RUNTIME_PREVIEW_RUN_ROOT/config/easypanel/verify-sell-isolation.sh" >/dev/null
runtime_preview_verify_service_snapshot "$RUNTIME_PREVIEW_RUN_ROOT/protected-services.before.tsv"

if [[ "${RUN_RUNTIME_PREVIEW_E2E:-0}" == "1" ]]; then
  compose_file="$RUNTIME_PREVIEW_RUN_ROOT/config/easypanel/compose.runtime-preview.yaml"
  unset COMPOSE_PROJECT_NAME COMPOSE_FILE COMPOSE_PROFILES COMPOSE_ENV_FILES
  compose=(docker compose --project-name "$RUNTIME_PREVIEW_COMPOSE_PROJECT" --env-file "$RUNTIME_PREVIEW_ENV_FILE" -f "$compose_file")
  runtime_preview_verify_public_attestation
  runtime_preview_ensure_owned_volume \
    "$RUNTIME_PREVIEW_EVIDENCE_VOLUME" runtime_evidence runtime-e2e-evidence
  runtime_preview_ensure_owned_volume \
    "$RUNTIME_PREVIEW_E2E_TRANSPORT_VOLUME" runtime_e2e_transport runtime-e2e-transport

  bridge_name="sell-autonomia-runtime-preview-e2e-bridge-${source_sha:0:12}-$BASHPID"
  bridge_id=""
  bridge_launch_attempted=0
  remove_e2e_network() {
    local network_json network_names
    if ! network_json="$(docker network inspect "$RUNTIME_PREVIEW_E2E_NETWORK" 2>/dev/null)"; then
      network_names="$(docker network ls --format '{{.Name}}')" || {
        echo "could not inventory Docker networks after inspect failure" >&2
        return 1
      }
      if grep -Fxq "$RUNTIME_PREVIEW_E2E_NETWORK" <<<"$network_names"; then
        echo "public-E2E network exists but could not be inspected" >&2
        return 1
      fi
      return 0
    fi
    jq -e \
      --arg network "$RUNTIME_PREVIEW_E2E_NETWORK" \
      --arg project "$RUNTIME_PREVIEW_PROJECT" \
      --arg compose_project "$RUNTIME_PREVIEW_COMPOSE_PROJECT" \
      --arg source_sha "$source_sha" '
        type == "array" and length == 1 and
        .[0].Name == $network and .[0].Internal == false and
        .[0].Labels["com.docker.compose.project"] == $compose_project and
        .[0].Labels["com.docker.compose.network"] == "sell-public-e2e" and
        .[0].Labels["com.autonomia.project"] == $project and
        .[0].Labels["com.autonomia.issue"] == "77" and
        .[0].Labels["com.autonomia.source-sha"] == $source_sha and
        (.[0].Containers | type == "object" and length == 0)
      ' <<<"$network_json" >/dev/null || {
      echo "refusing to remove an untrusted public-E2E network" >&2
      return 1
    }
    docker network rm "$RUNTIME_PREVIEW_E2E_NETWORK" >/dev/null || return 1
    network_names="$(docker network ls --format '{{.Name}}')" || return 1
    ! grep -Fxq "$RUNTIME_PREVIEW_E2E_NETWORK" <<<"$network_names" || {
      echo "public-E2E network remained after removal" >&2
      return 1
    }
  }
  cleanup_bridge() {
    local container_names bridge_inspect
    if [[ -z "$bridge_id" && "$bridge_launch_attempted" == "1" ]]; then
      if bridge_inspect="$(docker inspect "$bridge_name" 2>/dev/null)"; then
        bridge_id="$(jq -er '.[0].Id' <<<"$bridge_inspect")" || return 1
      else
        container_names="$(docker ps -a --format '{{.Names}}')" || {
          echo "could not inventory E2E bridge after inspect failure" >&2
          return 1
        }
        if grep -Fxq "$bridge_name" <<<"$container_names"; then
          echo "E2E bridge exists but could not be inspected" >&2
          return 1
        fi
      fi
    fi
    if [[ -n "$bridge_id" ]]; then
      if bridge_inspect="$(docker inspect "$bridge_id" 2>/dev/null)"; then
      jq -e \
        --arg id "$bridge_id" \
        --arg name "/$bridge_name" \
        --arg project "$RUNTIME_PREVIEW_PROJECT" \
        --arg compose_project "$RUNTIME_PREVIEW_COMPOSE_PROJECT" \
        --arg source_sha "$source_sha" '
          type == "array" and length == 1 and
          .[0].Id == $id and .[0].Name == $name and
          .[0].Config.Labels["com.docker.compose.project"] == $compose_project and
          .[0].Config.Labels["com.docker.compose.service"] == "e2e-bridge" and
          .[0].Config.Labels["com.autonomia.project"] == $project and
          .[0].Config.Labels["com.autonomia.issue"] == "77" and
          .[0].Config.Labels["com.autonomia.source-sha"] == $source_sha
        ' <<<"$bridge_inspect" >/dev/null || {
        echo "refusing to stop an untrusted E2E bridge container" >&2
        return 1
      }
      docker stop --time 10 "$bridge_id" >/dev/null
      for _bridge_remove_attempt in $(seq 1 30); do
        if ! docker inspect "$bridge_id" >/dev/null 2>&1; then break; fi
        sleep 1
      done
      container_names="$(docker ps -a --format '{{.Names}}')" || return 1
      ! grep -Fxq "$bridge_name" <<<"$container_names" || {
        echo "E2E bridge container did not auto-remove" >&2
        return 1
      }
      else
        container_names="$(docker ps -a --format '{{.Names}}')" || return 1
        ! grep -Fxq "$bridge_name" <<<"$container_names" || {
          echo "E2E bridge exists but could not be inspected" >&2
          return 1
        }
      fi
    fi
    bridge_id=""
    remove_e2e_network
  }
  cleanup_bridge_on_exit() {
    cleanup_status=$?
    trap - EXIT
    cleanup_bridge || cleanup_status=1
    exit "$cleanup_status"
  }
  trap cleanup_bridge_on_exit EXIT

  "${compose[@]}" --profile e2e run --pull never --rm evidence-init >/dev/null
  runtime_preview_verify_owned_volume \
    "$RUNTIME_PREVIEW_EVIDENCE_VOLUME" runtime_evidence runtime-e2e-evidence yes
  "${compose[@]}" --profile e2e run --pull never --rm --no-deps e2e-transport-init >/dev/null
  runtime_preview_verify_owned_volume \
    "$RUNTIME_PREVIEW_E2E_TRANSPORT_VOLUME" runtime_e2e_transport runtime-e2e-transport yes
  remove_e2e_network
  if docker inspect "$bridge_name" >/dev/null 2>&1; then
    echo "refusing to reuse an existing E2E bridge container name" >&2
    exit 1
  fi
  container_names="$(docker ps -a --format '{{.Names}}')" || {
    echo "could not inventory E2E bridge before launch" >&2
    exit 1
  }
  ! grep -Fxq "$bridge_name" <<<"$container_names" || {
    echo "E2E bridge exists but could not be inspected before launch" >&2
    exit 1
  }
  bridge_launch_attempted=1
  "${compose[@]}" --profile e2e run --pull never --rm -d --no-deps \
    --name "$bridge_name" e2e-bridge >/dev/null
  bridge_id="$(docker inspect --format '{{.Id}}' "$bridge_name")"
  bridge_ready=0
  for _bridge_ready_attempt in $(seq 1 30); do
    bridge_state="$(docker inspect --format '{{.State.Status}}:{{if .State.Health}}{{.State.Health.Status}}{{end}}:{{.RestartCount}}' "$bridge_id" 2>/dev/null || true)"
    if [[ "$bridge_state" == "running:healthy:0" ]]; then
      bridge_ready=1
      break
    fi
    sleep 1
  done
  [[ "$bridge_ready" == "1" ]] || {
    echo "runtime-preview E2E bridge did not become healthy" >&2
    exit 1
  }
  bridge_inspect="$(docker inspect "$bridge_id")"
  runtime_preview_inspect_has_exact_network "$bridge_inspect" "$RUNTIME_PREVIEW_E2E_NETWORK" || {
    echo "runtime-preview E2E bridge escaped its dedicated network" >&2
    exit 1
  }
  runtime_preview_verify_e2e_network "$bridge_id"
  jq -e \
    --arg image "autonomia-sell-preview-sell:$source_sha" \
    --arg network "$RUNTIME_PREVIEW_E2E_NETWORK" \
    --arg volume "$RUNTIME_PREVIEW_E2E_TRANSPORT_VOLUME" '
      type == "array" and length == 1 and
      .[0].Config.Image == $image and
      .[0].HostConfig.NetworkMode == $network and
      ([.[0].Config.Env[] | select(startswith("E2E_RUNTIME_BRIDGE_SOCKET="))] == ["E2E_RUNTIME_BRIDGE_SOCKET=/transport/sell.sock"]) and
      ([.[0].Config.Env[] | select(startswith("APPSELL_"))] == []) and
      (.[0].Mounts | length == 1 and .[0].Type == "volume" and .[0].Name == $volume and .[0].Destination == "/transport" and .[0].RW == true)
    ' <<<"$bridge_inspect" >/dev/null

  "${compose[@]}" --profile e2e run --pull never --rm --no-deps e2e
  "${compose[@]}" --profile e2e run --pull never --rm --no-deps --entrypoint sh e2e -ec \
    'test -s /evidence/current/admin-dashboard.png && test -s /evidence/current/buyer-player.png && test -s /evidence/current/superadmin-inspection.png'
  cleanup_bridge
  "${compose[@]}" --profile e2e run --pull never --rm --no-deps --entrypoint sh e2e -ec \
    'test ! -e /transport/sell.sock && test ! -L /transport/sell.sock && test -z "$(find /transport -mindepth 1 -maxdepth 1 -print -quit)"'
  runtime_preview_verify_owned_volume \
    "$RUNTIME_PREVIEW_E2E_TRANSPORT_VOLUME" runtime_e2e_transport runtime-e2e-transport yes
  runtime_preview_verify_owned_volume \
    "$RUNTIME_PREVIEW_EVIDENCE_VOLUME" runtime_evidence runtime-e2e-evidence yes

  domain_id="$(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" --filter 'label=com.docker.compose.service=domain-api')"
  bootstrap_id="$(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" --filter 'label=com.docker.compose.service=domain-bootstrap')"
  for stability_attempt in $(seq 1 7); do
    if [[ "$stability_attempt" -gt 1 ]]; then sleep 5; fi
    mapfile -t stable_ids < <(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT")
    stable_container_ids="$(printf '%s\n' "${stable_ids[@]}" | LC_ALL=C sort)"
    [[ ${#stable_ids[@]} -eq 3 && "$stable_container_ids" == "$baseline_container_ids" ]] || {
      echo "runtime-preview container identity changed during or after E2E" >&2
      exit 1
    }
    for service in domain-bootstrap domain-api sell-web; do
      mapfile -t lifecycle_ids < <(docker ps -aq \
        --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" \
        --filter "label=com.docker.compose.service=$service")
      [[ ${#lifecycle_ids[@]} -eq 1 ]] || {
        echo "$service does not have exactly one lifecycle target after E2E" >&2
        exit 1
      }
      current_lifecycle="$(runtime_preview_lifecycle_fingerprint_from_inspect "$(docker inspect "${lifecycle_ids[0]}")")"
      [[ "$current_lifecycle" == "${baseline_lifecycle[$service]}" ]] || {
        echo "$service lifecycle changed during or after E2E" >&2
        exit 1
      }
    done
    [[ "$(docker inspect --format '{{.State.Status}}:{{.State.Health.Status}}:{{.RestartCount}}' "$domain_id")" == "running:healthy:0" ]] || {
      echo "domain-api lost zero-restart health during or after E2E" >&2
      exit 1
    }
    [[ "$(docker inspect --format '{{.State.Status}}:{{.State.Health.Status}}:{{.RestartCount}}' "$sell_id")" == "running:healthy:0" ]] || {
      echo "sell-web lost zero-restart health during or after E2E" >&2
      exit 1
    }
    [[ "$(docker inspect --format '{{.State.Status}}:{{.State.ExitCode}}' "$bootstrap_id")" == "exited:0" ]] || {
      echo "domain-bootstrap changed during or after E2E" >&2
      exit 1
    }
    stable_ports="$(docker inspect --format '{{json .NetworkSettings.Ports}}' "$sell_id")"
    jq -e '."13200/tcp" | length == 1 and .[0].HostIp == "127.0.0.1" and .[0].HostPort == "13200"' \
      <<<"$stable_ports" >/dev/null
    curl -fsS --connect-timeout 3 --max-time 15 http://127.0.0.1:13200/ >/dev/null
    runtime_preview_verify_service_snapshot "$RUNTIME_PREVIEW_RUN_ROOT/protected-services.before.tsv"
  done
  runtime_preview_verify_attestation active
  runtime_preview_verify_live_record yes
  runtime_preview_verify_public_attestation
  cleanup_bridge
  trap - EXIT
  echo "runtime-preview remained healthy with stable IDs and zero restarts for 30 seconds after E2E"
fi
echo "runtime-preview health, image SHA, loopback port, exclusive network and protected-service invariants passed"
