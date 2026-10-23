#!/usr/bin/env bash
set -euo pipefail

# Creates the production asset-verification consumer from the already approved
# API service. Secrets stay inside Docker service metadata and are never echoed.

confirmation="${CONFIRM_APPSSELL_ASSET_WORKER:-}"
[[ "$confirmation" == "appsell-asset-worker-v1" ]] || {
  echo "set CONFIRM_APPSSELL_ASSET_WORKER=appsell-asset-worker-v1" >&2
  exit 2
}

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

api_service="${APPSSELL_API_SERVICE:-sell-autonomia_api}"
worker_service="${APPSSELL_ASSET_WORKER_SERVICE:-sell-autonomia_asset-worker}"
clamav_service="${APPSSELL_CLAMAV_SERVICE:-sell-autonomia_clamav}"

docker service inspect "$api_service" >/dev/null || {
  echo "API service not found: $api_service" >&2
  exit 1
}
docker service inspect "$clamav_service" >/dev/null || {
  echo "ClamAV service not found: $clamav_service" >&2
  exit 1
}

if docker service inspect "$worker_service" >/dev/null 2>&1; then
  echo "worker service already exists: $worker_service" >&2
  exit 1
fi

api_spec="$(docker service inspect "$api_service")"
worker_image="${APPSSELL_ASSET_WORKER_IMAGE:-}"
if [[ -z "$worker_image" ]]; then
  worker_image="$(jq -r '.[0].Spec.TaskTemplate.ContainerSpec.Image' <<<"$api_spec")"
fi
[[ -n "$worker_image" && "$worker_image" != "null" ]] || {
  echo "API image is missing" >&2
  exit 1
}

mapfile -t api_networks < <(
  jq -r '.[0].Spec.TaskTemplate.Networks[]?.Target' <<<"$api_spec" | sed '/^$/d'
)
[[ "${#api_networks[@]}" -gt 0 ]] || {
  echo "API service has no private network" >&2
  exit 1
}

mapfile -t clamav_networks < <(
  docker service inspect "$clamav_service" --format '{{range .Spec.TaskTemplate.Networks}}{{println .Target}}{{end}}' \
    | sed '/^$/d'
)
[[ "${#clamav_networks[@]}" -eq 1 ]] || {
  echo "ClamAV service must have exactly one private network" >&2
  exit 1
}
[[ " ${api_networks[*]} " == *" ${clamav_networks[0]} "* ]] || {
  echo "API and ClamAV do not share a private network" >&2
  exit 1
}
networks=("${clamav_networks[0]}")

# A rede isolada do storage (#461). Com a flag
# APPSELL_API_STORAGE_INTERNAL_HTTP_ALLOWED, o endpoint interno da API, que o
# worker copia, aponta para o alias appsell-storage, que só existe nesta rede.
# Ela vem da etapa 4 do docs/runbooks/storage-461-rede-interna.md, com OK: este
# script nunca a cria. Sem ela, o worker sobe sem storage.
storage_network="${APPSSELL_STORAGE_INTERNAL_NETWORK:-sell-storage-internal}"
if ! storage_network_shape="$(
  docker network inspect "$storage_network" --format '{{.Id}} {{.Driver}} {{.Internal}} {{.Scope}}'
)"; then
  echo "storage internal network not found: $storage_network (step 4 of docs/runbooks/storage-461-rede-interna.md)" >&2
  exit 1
fi
read -r storage_network_id storage_network_driver storage_network_internal storage_network_scope \
  <<<"$storage_network_shape"
[[ "$storage_network_driver $storage_network_internal $storage_network_scope" == "overlay true swarm" ]] || {
  echo "storage internal network must be an internal swarm overlay: $storage_network" >&2
  exit 1
}
if [[ "$storage_network_id" != "${clamav_networks[0]}" ]]; then
  networks+=("$storage_network_id")
fi

mount_source() {
  local target="$1"
  jq -r --arg target "$target" '
    .[0].Spec.TaskTemplate.ContainerSpec.Mounts[]?
    | select(.Type == "volume" and .Target == $target)
    | .Source
  ' <<<"$api_spec" | head -n 1
}

clamav_socket_volume="$(mount_source /run/appsell-clamav)"
pdf_socket_volume="$(mount_source /run/appsell-pdf)"
[[ -n "$clamav_socket_volume" && "$clamav_socket_volume" != "null" ]] || {
  echo "API ClamAV socket volume is missing" >&2
  exit 1
}
[[ -n "$pdf_socket_volume" && "$pdf_socket_volume" != "null" ]] || {
  echo "API PDF socket volume is missing" >&2
  exit 1
}

mapfile -t environment < <(
  jq -r '.[0].Spec.TaskTemplate.ContainerSpec.Env[]?' <<<"$api_spec"
)
[[ "${#environment[@]}" -gt 0 ]] || {
  echo "API environment is missing" >&2
  exit 1
}

env_value() {
  local key="$1" entry prefix="$1="
  for entry in "${environment[@]}"; do
    if [[ "$entry" == "$prefix"* ]]; then
      printf '%s' "${entry#"$prefix"}"
      return 0
    fi
  done
  return 1
}

required_environment=(
  APPSELL_API_ENVIRONMENT
  APPSELL_API_DATABASE_URL
  APPSELL_API_INTERNAL_ISSUER
  APPSELL_API_INTERNAL_AUDIENCE
  APPSELL_API_INTERNAL_HMAC_KEYRING_B64_JSON
  APPSELL_API_SERVICE_CREDENTIAL_B64
  APPSELL_API_STORAGE_PROVIDER
  APPSELL_API_STORAGE_INTERNAL_ENDPOINT
  APPSELL_API_STORAGE_CONTROL_ACCESS_KEY
  APPSELL_API_STORAGE_CONTROL_SECRET_KEY
  APPSELL_API_STORAGE_QUARANTINE_BUCKET
  APPSELL_API_STORAGE_READY_BUCKET
  APPSELL_API_STORAGE_ALLOWED_ORIGIN
  APPSELL_API_MALWARE_SCANNER_PROVIDER
  APPSELL_API_MALWARE_SCANNER_SOCKET
  APPSELL_API_PDF_SANDBOX_SOCKET
)
for key in "${required_environment[@]}"; do
  value="$(env_value "$key")" || {
    echo "required API environment is missing: $key" >&2
    exit 1
  }
  [[ -n "$value" ]] || {
    echo "required API environment is empty: $key" >&2
    exit 1
  }
done

[[ "$(env_value APPSELL_API_ENVIRONMENT)" == "production" ]] || {
  echo "API environment is not production" >&2
  exit 1
}
[[ "$(env_value APPSELL_API_MALWARE_SCANNER_PROVIDER)" == "clamav" ]] || {
  echo "production asset worker requires ClamAV" >&2
  exit 1
}
[[ "$(env_value APPSELL_API_PDF_SANDBOX_SOCKET)" == "/run/appsell-pdf/pdf.sock" ]] || {
  echo "unexpected PDF sandbox socket path" >&2
  exit 1
}

create_args=(
  service create
  --name "$worker_service"
  --label "com.autonomia.project=sell-autonomia"
  --label "com.autonomia.runtime=asset-worker"
  --label "com.autonomia.source-api=$api_service"
  --no-resolve-image
  --replicas 1
  # Memória e `/tmp` andam juntos: o `tmpfs` conta contra o cgroup, então o
  # limite de memória precisa cobrir os arquivos e os processos ao mesmo tempo.
  #
  # Pior caso de um PDF de 300 páginas, medido e somado:
  #
  #     PDF de origem                     100 MiB
  #     pacote recebido                   486 MiB
  #     cópia do pacote na verificação    486 MiB
  #     páginas extraídas                 384 MiB
  #     PDF extraído do pacote            100 MiB
  #     ------------------------------------------
  #     em disco                        1.556 MiB   + ~200 MiB do processo
  #
  # Eram 384 MiB de `/tmp` e 1 GiB de memória: um documento grande falhava com
  # disco cheio, e a mensagem não dizia isso.
  #
  # Nota: produção estava com 4 GiB de memória, ajustados à mão em algum
  # momento, enquanto este arquivo dizia 1 GiB — um redeploy pelo script teria
  # reduzido o serviço sem ninguém perceber.
  --limit-cpu 1.0
  --reserve-cpu 0.10
  --limit-memory 3G
  --reserve-memory 256M
  --limit-pids 64
  --restart-condition on-failure
  --restart-delay 5s
  --restart-window 30s
  --update-parallelism 1
  --update-failure-action pause
  --update-max-failure-ratio 0
  --update-monitor 5s
  --update-order start-first
  --rollback-parallelism 1
  --rollback-failure-action pause
  --rollback-max-failure-ratio 0
  --rollback-monitor 5s
  --rollback-order stop-first
  --stop-grace-period 30s
  --init
  --read-only
  --cap-drop ALL
  --mount "type=volume,source=$clamav_socket_volume,destination=/run/appsell-clamav,readonly=true"
  --mount "type=volume,source=$pdf_socket_volume,destination=/run/appsell-pdf,readonly=true"
  --mount "type=tmpfs,destination=/tmp,tmpfs-size=2147483648,tmpfs-mode=1777"
  --health-cmd 'python -c "from appsell_api.config import get_settings; s=get_settings(); assert s.environment == \"production\" and s.storage_provider == \"s3\" and s.malware_scanner_provider == \"clamav\" and s.pdf_sandbox_socket"'
  --health-interval 15s
  --health-timeout 5s
  --health-retries 5
  --health-start-period 30s
)

for network in "${networks[@]}"; do
  create_args+=(--network "$network")
done
for entry in "${environment[@]}"; do
  create_args+=(--env "$entry")
done

create_args+=(--entrypoint python "$worker_image" -m appsell_api.asset_worker_service)

echo "creating $worker_service from $worker_image"
docker "${create_args[@]}"
echo "asset worker service created: $worker_service"
