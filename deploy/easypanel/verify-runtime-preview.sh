#!/usr/bin/env bash
set -euo pipefail
case $- in *x*) set +x ;; esac

[[ $# -eq 3 ]] || { echo "Usage: $0 RUN_ROOT ENV_FILE APPSELL_SOURCE_SHA" >&2; exit 2; }
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-preview-common.sh
source "$here/runtime-preview-common.sh"
runtime_preview_require_root
runtime_preview_require_commands curl date dig docker jq openssl realpath sha256sum stat timeout
runtime_preview_assert_run_root "$1" "$3"
runtime_preview_assert_env_file "$2"
source_sha="$3"
compose_file="$RUNTIME_PREVIEW_RUN_ROOT/config/easypanel/compose.runtime-preview.yaml"
expected_ingress_command_json="$(env \
  -u COMPOSE_PROJECT_NAME -u COMPOSE_FILE -u COMPOSE_PROFILES -u COMPOSE_ENV_FILES \
  docker compose --project-name "$RUNTIME_PREVIEW_COMPOSE_PROJECT" \
  --env-file "$RUNTIME_PREVIEW_ENV_FILE" -f "$compose_file" config --format json \
  | jq -ce '.services["ingress-proxy"].command')"
runtime_preview_api_login
[[ "$(runtime_preview_service_state)" == "present" ]] || { echo "EasyPanel runtime-preview is absent" >&2; exit 1; }
runtime_preview_verify_attestation 'configured|active'
runtime_preview_verify_live_record yes

ready=0
for _attempt in $(seq 1 60); do
  mapfile -t ids < <(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT")
  if [[ ${#ids[@]} -eq 6 ]]; then
    domain_state="$(docker inspect --format '{{.State.Status}}:{{if .State.Health}}{{.State.Health.Status}}{{end}}:{{.RestartCount}}' \
      "$(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" --filter 'label=com.docker.compose.service=domain-api')" 2>/dev/null || true)"
    web_state="$(docker inspect --format '{{.State.Status}}:{{if .State.Health}}{{.State.Health.Status}}{{end}}:{{.RestartCount}}' \
      "$(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" --filter 'label=com.docker.compose.service=sell-web')" 2>/dev/null || true)"
    bootstrap_state="$(docker inspect --format '{{.State.Status}}:{{.State.ExitCode}}' \
      "$(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" --filter 'label=com.docker.compose.service=domain-bootstrap')" 2>/dev/null || true)"
    ingress_state="$(docker inspect --format '{{.State.Status}}:{{if .State.Health}}{{.State.Health.Status}}{{end}}:{{.RestartCount}}' \
      "$(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" --filter 'label=com.docker.compose.service=ingress-proxy')" 2>/dev/null || true)"
    pdf_init_state="$(docker inspect --format '{{.State.Status}}:{{.State.ExitCode}}' \
      "$(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" --filter 'label=com.docker.compose.service=pdf-sandbox-init')" 2>/dev/null || true)"
    pdf_state="$(docker inspect --format '{{.State.Status}}:{{if .State.Health}}{{.State.Health.Status}}{{end}}:{{.RestartCount}}' \
      "$(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" --filter 'label=com.docker.compose.service=pdf-sandbox')" 2>/dev/null || true)"
    if [[ "$domain_state" == "running:healthy:0" && "$web_state" == "running:healthy:0" && \
      "$ingress_state" == "running:healthy:0" && "$pdf_state" =~ ^running:healthy:[0-9]+$ && \
      "$bootstrap_state" == "exited:0" && "$pdf_init_state" == "exited:0" ]]; then
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
  case "$service" in domain-bootstrap|domain-api|pdf-sandbox-init|pdf-sandbox|sell-web|ingress-proxy) ;; *) echo "unexpected runtime service: $service" >&2; exit 1 ;; esac
  [[ -z "${seen[$service]:-}" ]] || exit 1
  seen[$service]=1
  baseline_lifecycle[$service]="$(runtime_preview_lifecycle_fingerprint_from_inspect "$(docker inspect "$id")")"
  image="$(docker inspect --format '{{.Config.Image}}' "$id")"
  case "$service" in
    domain-bootstrap|domain-api|pdf-sandbox-init|pdf-sandbox|ingress-proxy) [[ "$image" == "autonomia-sell-preview-domain:$source_sha" ]] ;;
    sell-web) [[ "$image" == "autonomia-sell-preview-sell:$source_sha" ]] ;;
  esac || { echo "unexpected image for $service" >&2; exit 1; }
  if [[ "$service" == "domain-bootstrap" || "$service" == "pdf-sandbox-init" ]]; then
    [[ "$(docker inspect --format '{{.State.Status}}:{{.State.ExitCode}}' "$id")" == "exited:0" ]] || exit 1
  elif [[ "$service" == "pdf-sandbox" ]]; then
    [[ "$(docker inspect --format '{{.State.Status}}:{{.State.Health.Status}}' "$id")" == "running:healthy" ]] || exit 1
  else
    [[ "$(docker inspect --format '{{.State.Status}}:{{.State.Health.Status}}:{{.RestartCount}}' "$id")" == "running:healthy:0" ]] || exit 1
  fi
done
[[ ${#seen[@]} -eq 6 ]] || exit 1
domain_api_id="$(docker ps -q --no-trunc \
  --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" \
  --filter 'label=com.docker.compose.service=domain-api')"
[[ -n "$domain_api_id" ]] || { echo "domain-api container identity is missing" >&2; exit 1; }
domain_image_id="$(docker inspect --format '{{.Image}}' "$domain_api_id")"
domain_image_json="$(docker image inspect "$domain_image_id")"
jq -e --arg image_id "$domain_image_id" --arg source_sha "$source_sha" '
  type == "array" and length == 1 and .[0].Id == $image_id and
  .[0].Config.Labels["org.opencontainers.image.revision"] == $source_sha
' <<<"$domain_image_json" >/dev/null || {
  echo "domain runtime image identity or OCI revision drifted" >&2
  exit 1
}
domain_image_env_json="$(jq -ce '
  .[0].Config.Env |
  if . == null then .
  elif type == "array" and all(.[]; type == "string") then .
  else error("domain runtime image environment is invalid")
  end
' <<<"$domain_image_json")"

for service in domain-bootstrap domain-api pdf-sandbox-init pdf-sandbox sell-web ingress-proxy; do
  mapfile -t service_ids < <(docker ps -aq \
    --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" \
    --filter "label=com.docker.compose.service=$service")
  [[ ${#service_ids[@]} -eq 1 ]] || {
    echo "$service does not have exactly one runtime-preview container" >&2
    exit 1
  }
  inspect_json="$(docker inspect "${service_ids[0]}")"
  case "$service" in
    domain-bootstrap|domain-api)
      runtime_preview_inspect_has_exact_network "$inspect_json" "$RUNTIME_PREVIEW_NETWORK" || {
        echo "$service escaped the exclusive sell network" >&2
        exit 1
      }
      if [[ "$service" == "domain-api" ]]; then
        jq -e --arg volume "$RUNTIME_PREVIEW_PDF_SOCKET_VOLUME" '
          (.[0].Mounts | length == 1 and .[0].Type == "volume" and
            .[0].Name == $volume and .[0].Destination == "/run/appsell-pdf" and
            .[0].RW == false) and
          (.[0].Config.Env | index("APPSELL_API_STORAGE_PROVIDER=disabled")) != null and
          (.[0].Config.Env | index("APPSELL_API_PDF_SANDBOX_SOCKET=/run/appsell-pdf/pdf.sock")) != null and
          (.[0].Config.Env | index("APPSELL_API_PRIVATE_LEARNING_ASSET_ROOT=/workspace/sell/output/pdf/codex")) != null
        ' <<<"$inspect_json" >/dev/null || {
          echo "domain-api protected PDF mount or configuration drifted" >&2
          exit 1
        }
      fi
      ;;
    pdf-sandbox-init|pdf-sandbox)
      jq -e --arg domain_image_id "$domain_image_id" \
        --arg volume "$RUNTIME_PREVIEW_PDF_SOCKET_VOLUME" \
        --argjson domain_image_env "$domain_image_env_json" '
        .[0].HostConfig.NetworkMode == "none" and
        (.[0].NetworkSettings.Networks == null or .[0].NetworkSettings.Networks == {}) and
        .[0].Image == $domain_image_id and
        .[0].HostConfig.Privileged == false and
        .[0].HostConfig.ReadonlyRootfs == true and
        .[0].HostConfig.SecurityOpt == ["no-new-privileges:true"] and
        .[0].HostConfig.CapDrop == ["ALL"] and
        .[0].HostConfig.PortBindings == {} and
        (.[0].Mounts | length == 1 and .[0].Type == "volume" and
          .[0].Name == $volume and .[0].Destination == "/run/appsell-pdf" and .[0].RW == true)
      ' <<<"$inspect_json" >/dev/null || {
        echo "$service escaped the networkless PDF sandbox boundary" >&2
        exit 1
      }
      if [[ "$service" == "pdf-sandbox-init" ]]; then
        jq -e --argjson domain_image_env "$domain_image_env_json" '
          .[0].Config.Env == $domain_image_env and
          .[0].Config.User == "0:0" and .[0].HostConfig.CapAdd == ["CHOWN"] and
          .[0].HostConfig.RestartPolicy == {"Name":"no","MaximumRetryCount":0} and
          .[0].HostConfig.Memory == 33554432 and .[0].HostConfig.NanoCpus == 50000000 and
          .[0].HostConfig.PidsLimit == 32
        ' <<<"$inspect_json" >/dev/null || {
          echo "pdf-sandbox-init privilege or resource boundary drifted" >&2
          exit 1
        }
      else
        jq -e --argjson domain_image_env "$domain_image_env_json" '
          ([.[0].Config.Env[] | select(startswith("APPSELL_"))] ==
            ["APPSELL_PDF_SANDBOX_RECYCLE_AFTER_JOB=true"]) and
          ((.[0].Config.Env - ["APPSELL_PDF_SANDBOX_RECYCLE_AFTER_JOB=true"]) ==
            $domain_image_env) and
          .[0].Config.User == "10001:10001" and .[0].HostConfig.CapAdd == null and
          .[0].Config.Cmd == ["python","-m","appsell_api.pdf_sandbox_runner"] and
          .[0].HostConfig.RestartPolicy == {"Name":"unless-stopped","MaximumRetryCount":0} and
          .[0].HostConfig.Memory == 805306368 and .[0].HostConfig.NanoCpus == 1000000000 and
          .[0].HostConfig.PidsLimit == 96 and
          (.[0].HostConfig.Tmpfs | keys | sort) == ["/tmp","/workspace/sell/output/pdf"] and
          (.[0].HostConfig.Tmpfs["/workspace/sell/output/pdf"] | contains("mode=000"))
        ' <<<"$inspect_json" >/dev/null || {
          echo "pdf-sandbox identity, command, resource or content mask drifted" >&2
          exit 1
        }
      fi
      ;;
    sell-web)
      jq -e --arg private "$RUNTIME_PREVIEW_NETWORK" --arg ingress "$RUNTIME_PREVIEW_INGRESS_NETWORK" '
        (.[0].NetworkSettings.Networks | keys | sort) == ([$private,$ingress] | sort) and
        ((.[0].NetworkSettings.Networks[$ingress].Aliases // []) | index("sell-web-ingress-77")) != null
      ' <<<"$inspect_json" >/dev/null || {
        echo "sell-web escaped its private plus dedicated ingress networks" >&2
        exit 1
      }
      ;;
    ingress-proxy)
      jq -e --arg ingress "$RUNTIME_PREVIEW_INGRESS_NETWORK" \
        --arg public "$RUNTIME_PREVIEW_EASYPANEL_NETWORK" --arg alias "$RUNTIME_PREVIEW_INGRESS_ALIAS" \
        --arg domain_image_id "$domain_image_id" --arg source_sha "$source_sha" \
        --argjson domain_image_env "$domain_image_env_json" \
        --argjson expected_cmd "$expected_ingress_command_json" '
        (.[0].NetworkSettings.Networks | keys | sort) == ([$ingress,$public] | sort) and
        ((.[0].NetworkSettings.Networks[$public].Aliases // []) | index($alias)) != null and
        .[0].Config.Env == $domain_image_env and
        .[0].Image == $domain_image_id and
        .[0].Config.Image == ("autonomia-sell-preview-domain:" + $source_sha) and
        .[0].Config.Cmd == $expected_cmd and .[0].Config.Entrypoint == null and
        .[0].Config.User == "10001:10001" and
        .[0].Config.Labels["com.autonomia.purpose"] == "ingress-proxy" and
        .[0].HostConfig.ReadonlyRootfs == true and .[0].HostConfig.Privileged == false and
        .[0].HostConfig.SecurityOpt == ["no-new-privileges:true"] and .[0].HostConfig.CapDrop == ["ALL"] and
        .[0].HostConfig.RestartPolicy == {"Name":"unless-stopped","MaximumRetryCount":0} and
        .[0].HostConfig.Memory == 67108864 and .[0].HostConfig.NanoCpus == 100000000 and
        .[0].HostConfig.PidsLimit == 64 and
        .[0].HostConfig.LogConfig == {"Type":"json-file","Config":{"max-file":"3","max-size":"10m"}} and
        .[0].Mounts == [] and .[0].HostConfig.PortBindings == {}
      ' <<<"$inspect_json" >/dev/null || {
        echo "ingress-proxy runtime identity or secretless topology drifted" >&2
        exit 1
      }
      ;;
  esac
done
sell_id="$(docker ps -q --no-trunc --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" --filter 'label=com.docker.compose.service=sell-web')"
ingress_id="$(docker ps -q --no-trunc --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" --filter 'label=com.docker.compose.service=ingress-proxy')"
pdf_id="$(docker ps -q --no-trunc --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" --filter 'label=com.docker.compose.service=pdf-sandbox')"
pdf_restart_count_before_e2e="$(docker inspect --format '{{.RestartCount}}' "$pdf_id")"
pdf_planned_recycles_before_e2e="$(docker logs "$pdf_id" 2>&1 | \
  awk '/^\{"event":"appsell_pdf_worker_planned_recycle"\}$/{count++} END{print count+0}')"
pdf_events_since="$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)"
ingress_network_json="$(docker network inspect "$RUNTIME_PREVIEW_INGRESS_NETWORK")"
jq -e \
  --arg network "$RUNTIME_PREVIEW_INGRESS_NETWORK" --arg project "$RUNTIME_PREVIEW_PROJECT" \
  --arg compose_project "$RUNTIME_PREVIEW_COMPOSE_PROJECT" --arg source_sha "$source_sha" \
  --arg sell_id "$sell_id" --arg ingress_id "$ingress_id" '
    type == "array" and length == 1 and .[0].Name == $network and
    .[0].Internal == true and
    .[0].Labels["com.docker.compose.project"] == $compose_project and
    .[0].Labels["com.docker.compose.network"] == "sell-ingress" and
    .[0].Labels["com.autonomia.project"] == $project and
    .[0].Labels["com.autonomia.issue"] == "77" and
    .[0].Labels["com.autonomia.purpose"] == "ingress-proxy" and
    .[0].Labels["com.autonomia.source-sha"] == $source_sha and
    (.[0].Containers | keys | sort) == ([$sell_id,$ingress_id] | sort)
  ' <<<"$ingress_network_json" >/dev/null || {
  echo "dedicated ingress network ownership or membership drifted" >&2
  exit 1
}
global_network_json="$(docker network inspect "$RUNTIME_PREVIEW_EASYPANEL_NETWORK")"
runtime_preview_verify_easypanel_ingress_network_json "$global_network_json" "$ingress_id" || {
  echo "EasyPanel ingress network, Traefik membership or proxy endpoint drifted" >&2
  exit 1
}
mapfile -t global_container_ids < <(runtime_preview_easypanel_ingress_container_ids "$global_network_json")
global_alias_count=0
for global_id in "${global_container_ids[@]}"; do
  global_container_json="$(docker inspect "$global_id")" || {
    echo "could not inspect an EasyPanel ingress endpoint" >&2
    exit 1
  }
  alias_occurrences="$(jq -er \
    --arg network "$RUNTIME_PREVIEW_EASYPANEL_NETWORK" --arg alias "$RUNTIME_PREVIEW_INGRESS_ALIAS" '
      [.[0].NetworkSettings.Networks[$network].Aliases[]? | select(. == $alias)] | length
    ' <<<"$global_container_json")"
  if (( alias_occurrences > 0 )); then
    jq -e --arg id "$ingress_id" --arg network "$RUNTIME_PREVIEW_EASYPANEL_NETWORK" \
      --arg alias "$RUNTIME_PREVIEW_INGRESS_ALIAS" --arg project "$RUNTIME_PREVIEW_COMPOSE_PROJECT" '
        type == "array" and length == 1 and .[0].Id == $id and
        .[0].Config.Labels["com.docker.compose.project"] == $project and
        .[0].Config.Labels["com.docker.compose.service"] == "ingress-proxy" and
        .[0].Config.Labels["com.autonomia.purpose"] == "ingress-proxy" and
        ([.[0].NetworkSettings.Networks[$network].Aliases[]? | select(. == $alias)] | length) > 0
      ' <<<"$global_container_json" >/dev/null || {
      echo "EasyPanel runtime-preview ingress alias belongs to an unexpected endpoint" >&2
      exit 1
    }
    global_alias_count=$((global_alias_count + alias_occurrences))
  fi
done
[[ "$global_alias_count" == "1" ]] || {
  echo "EasyPanel runtime-preview ingress alias must appear exactly once" >&2
  exit 1
}
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
runtime_preview_verify_owned_volume \
  "$RUNTIME_PREVIEW_PDF_SOCKET_VOLUME" runtime_pdf_socket pdf-raster-socket no
mapfile -t pdf_volume_consumers < <(docker ps -aq --filter "volume=$RUNTIME_PREVIEW_PDF_SOCKET_VOLUME")
[[ ${#pdf_volume_consumers[@]} -eq 3 ]] || {
  echo "PDF socket volume must have exactly three owned consumers" >&2
  exit 1
}
pdf_volume_services="$(for id in "${pdf_volume_consumers[@]}"; do
  docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$id"
done | LC_ALL=C sort)"
[[ "$pdf_volume_services" == $'domain-api\npdf-sandbox\npdf-sandbox-init' ]] || {
  echo "PDF socket volume consumer scope drifted" >&2
  exit 1
}

if [[ "${RUN_RUNTIME_PREVIEW_E2E:-0}" == "1" ]]; then
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
  pdf_init_id="$(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" --filter 'label=com.docker.compose.service=pdf-sandbox-init')"
  sleep 3
  pdf_restart_count_after_e2e=""
  for stability_attempt in $(seq 1 7); do
    if [[ "$stability_attempt" -gt 1 ]]; then sleep 5; fi
    mapfile -t stable_ids < <(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT")
    stable_container_ids="$(printf '%s\n' "${stable_ids[@]}" | LC_ALL=C sort)"
    [[ ${#stable_ids[@]} -eq 6 && "$stable_container_ids" == "$baseline_container_ids" ]] || {
      echo "runtime-preview container identity changed during or after E2E" >&2
      exit 1
    }
    for service in domain-bootstrap domain-api pdf-sandbox-init pdf-sandbox sell-web ingress-proxy; do
      mapfile -t lifecycle_ids < <(docker ps -aq \
        --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" \
        --filter "label=com.docker.compose.service=$service")
      [[ ${#lifecycle_ids[@]} -eq 1 ]] || {
        echo "$service does not have exactly one lifecycle target after E2E" >&2
        exit 1
      }
      current_lifecycle="$(runtime_preview_lifecycle_fingerprint_from_inspect "$(docker inspect "${lifecycle_ids[0]}")")"
      if [[ "$service" == "pdf-sandbox" ]]; then
        continue
      fi
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
    [[ "$(docker inspect --format '{{.State.Status}}:{{.State.Health.Status}}:{{.RestartCount}}' "$ingress_id")" == "running:healthy:0" ]] || {
      echo "ingress-proxy lost zero-restart health during or after E2E" >&2
      exit 1
    }
    [[ "$(docker inspect --format '{{.State.Status}}:{{.State.ExitCode}}' "$bootstrap_id")" == "exited:0" ]] || {
      echo "domain-bootstrap changed during or after E2E" >&2
      exit 1
    }
    pdf_state_after_e2e="$(docker inspect --format '{{.State.Status}}:{{.State.Health.Status}}:{{.RestartCount}}' "$pdf_id")"
    [[ "$pdf_state_after_e2e" =~ ^running:healthy:([0-9]+)$ ]] || {
      echo "pdf-sandbox lost healthy state during or after E2E" >&2
      exit 1
    }
    pdf_current_restart_count="${BASH_REMATCH[1]}"
    pdf_current_planned_recycles="$(docker logs "$pdf_id" 2>&1 | \
      awk '/^\{"event":"appsell_pdf_worker_planned_recycle"\}$/{count++} END{print count+0}')"
    if [[ -z "$pdf_restart_count_after_e2e" ]]; then
      pdf_restart_count_after_e2e="$pdf_current_restart_count"
      pdf_planned_recycles_after_e2e="$pdf_current_planned_recycles"
      pdf_events_until="$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)"
      if ! pdf_die_events="$(runtime_preview_docker_die_exit_codes \
        "$pdf_id" "$pdf_events_since" "$pdf_events_until")"; then
        echo "failed to read pdf-sandbox die events" >&2
        exit 1
      fi
      runtime_preview_verify_pdf_recycle_evidence \
        "$pdf_restart_count_before_e2e" "$pdf_restart_count_after_e2e" \
        "$pdf_planned_recycles_before_e2e" "$pdf_planned_recycles_after_e2e" \
        "$pdf_die_events"
    fi
    [[ "$pdf_current_restart_count" == "$pdf_restart_count_after_e2e" ]] || {
      echo "pdf-sandbox recycled unexpectedly after the media E2E completed" >&2
      exit 1
    }
    [[ "$pdf_current_planned_recycles" == "$pdf_planned_recycles_after_e2e" ]] || {
      echo "pdf-sandbox emitted an unexpected recycle marker after E2E" >&2
      exit 1
    }
    [[ "$(docker inspect --format '{{.State.Status}}:{{.State.ExitCode}}' "$pdf_init_id")" == "exited:0" ]] || {
      echo "pdf-sandbox-init changed during or after E2E" >&2
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
