#!/usr/bin/env bash

RUNTIME_PREVIEW_PROJECT="sell-autonomia"
RUNTIME_PREVIEW_SERVICE="runtime-preview"
RUNTIME_PREVIEW_COMPOSE_PROJECT="sell-autonomia_runtime-preview"
RUNTIME_PREVIEW_NETWORK="easypanel-sell-autonomia"
RUNTIME_PREVIEW_INGRESS_NETWORK="sell-autonomia-runtime-preview-ingress-77"
RUNTIME_PREVIEW_EASYPANEL_NETWORK="easypanel"
RUNTIME_PREVIEW_INGRESS_ALIAS="sell-autonomia_runtime-preview_sell-web"
RUNTIME_PREVIEW_TRANSITION_LOCK="/root/.sell-runtime-preview-77-transition.lock"
RUNTIME_PREVIEW_E2E_NETWORK="${RUNTIME_PREVIEW_COMPOSE_PROJECT}_sell-public-e2e"
RUNTIME_PREVIEW_EVIDENCE_VOLUME="${RUNTIME_PREVIEW_COMPOSE_PROJECT}_runtime_evidence"
RUNTIME_PREVIEW_E2E_TRANSPORT_VOLUME="${RUNTIME_PREVIEW_COMPOSE_PROJECT}_runtime_e2e_transport"
RUNTIME_PREVIEW_API_BASE="${EASYPANEL_API_BASE:-http://127.0.0.1:3000}"
RUNTIME_PREVIEW_PUBLIC_HOST="sell.autonomia.site"
RUNTIME_PREVIEW_PUBLIC_ORIGIN="https://sell.autonomia.site"
RUNTIME_PREVIEW_PUBLIC_IPV4="85.31.60.100"
RUNTIME_PREVIEW_VOLUME_ENVIRONMENT="local-ci-easypanel-preview"

runtime_preview_fail() {
  echo "$*" >&2
  return 1
}

runtime_preview_require_root() {
  [[ "$(id -u)" == "0" ]] || { runtime_preview_fail "runtime-preview operations must run as root"; return 1; }
}

runtime_preview_require_commands() {
  local command_name
  for command_name in "$@"; do
    command -v "$command_name" >/dev/null 2>&1 || {
      runtime_preview_fail "missing required command: $command_name"
      return 1
    }
  done
}

runtime_preview_acquire_transition_lock() {
  [[ $# -eq 1 ]] || return 2
  local lock_file="$1" lock_identity
  [[ -f "$lock_file" && ! -L "$lock_file" && "$(stat -c '%a:%u:%g:%h' "$lock_file")" == "600:0:0:1" ]] || {
    runtime_preview_fail "runtime-preview transition lock ownership drifted"
    return 1
  }
  lock_identity="$(stat -c '%d:%i:%a:%u:%g:%h' "$lock_file")" || return 1
  exec 8<>"$lock_file" || return 1
  [[ "$(stat -Lc '%d:%i:%a:%u:%g:%h' "/proc/$BASHPID/fd/8")" == "$lock_identity" ]] || return 1
  flock -n 8 || {
    runtime_preview_fail "runtime-preview transition is already running"
    return 1
  }
}

runtime_preview_verify_easypanel_ingress_network_json() {
  [[ $# -eq 2 ]] || return 2
  local network_json="$1" expected_ingress_id="$2"
  [[ -z "$expected_ingress_id" || "$expected_ingress_id" =~ ^[0-9a-f]{64}$ ]] || return 2
  jq -e --arg network "$RUNTIME_PREVIEW_EASYPANEL_NETWORK" --arg expected "$expected_ingress_id" '
    type == "array" and length == 1 and .[0].Name == $network and
    .[0].Driver == "overlay" and .[0].Scope == "swarm" and
    .[0].Attachable == true and .[0].Internal == false and
    (.[0].Containers | type == "object") and
    ((.[0].Containers | has("lb-easypanel") | not) or
      .[0].Containers["lb-easypanel"].Name == "easypanel-endpoint") and
    ([.[0].Containers | keys[] | select((test("^[0-9a-f]{64}$") or . == "lb-easypanel") | not)] | length) == 0 and
    ([.[0].Containers[]?.Name | select(test("^easypanel-traefik\\.1\\."))] | length) == 1 and
    ($expected == "" or (.[0].Containers | has($expected)))
  ' <<<"$network_json" >/dev/null
}

runtime_preview_easypanel_ingress_container_ids() {
  [[ $# -eq 1 ]] || return 2
  jq -r '.[0].Containers | keys[]? | select(test("^[0-9a-f]{64}$"))' <<<"$1"
}

runtime_preview_inspect_has_exact_network() {
  [[ $# -eq 2 ]] || return 2
  local inspect_json="$1" expected_network="$2"
  jq -e --arg expected "$expected_network" '
    type == "array" and
    length == 1 and
    (.[0].NetworkSettings.Networks | type == "object" and keys == [$expected])
  ' <<<"$inspect_json" >/dev/null
}

runtime_preview_lifecycle_fingerprint_from_inspect() {
  [[ $# -eq 1 ]] || return 2
  jq -er '
    if type == "array" and length == 1 then
      .[0] as $container |
      $container.Config.Labels["com.docker.compose.service"] as $service |
      if $service == "domain-bootstrap" or $service == "domain-api" or $service == "sell-web" or $service == "ingress-proxy" then
        [
          $service,
          $container.Id,
          $container.State.StartedAt,
          $container.State.FinishedAt,
          ($container.RestartCount | tostring),
          ($container.State.ExitCode | tostring)
        ] | join("|")
      else error("unexpected runtime-preview lifecycle service") end
    else error("invalid runtime-preview lifecycle inspection") end
  ' <<<"$1"
}

runtime_preview_verify_public_dns() {
  local a_records aaaa_records cname_records
  a_records="$(dig +short A "$RUNTIME_PREVIEW_PUBLIC_HOST")" || return 1
  aaaa_records="$(dig +short AAAA "$RUNTIME_PREVIEW_PUBLIC_HOST")" || return 1
  cname_records="$(dig +short CNAME "$RUNTIME_PREVIEW_PUBLIC_HOST")" || return 1
  [[ "$a_records" == "$RUNTIME_PREVIEW_PUBLIC_IPV4" && -z "$aaaa_records" && -z "$cname_records" ]] || {
    runtime_preview_fail "public DNS must contain only the approved IPv4 and no AAAA or CNAME"
    return 1
  }
}

runtime_preview_volume_inspect() {
  [[ $# -eq 1 ]] || return 2
  local volume="$1" output names
  if output="$(docker volume inspect "$volume" 2>/dev/null)"; then
    printf '%s' "$output"
    return 0
  fi
  names="$(docker volume ls --format '{{.Name}}')" || {
    runtime_preview_fail "could not inventory Docker volumes after inspect failure"
    return 1
  }
  if grep -Fxq "$volume" <<<"$names"; then
    runtime_preview_fail "Docker volume exists but could not be inspected"
    return 1
  fi
  return 3
}

runtime_preview_verify_owned_volume() {
  [[ $# -eq 4 ]] || return 2
  local volume="$1" compose_volume="$2" purpose="$3" require_unused="$4" inspect consumers
  inspect="$(runtime_preview_volume_inspect "$volume")" || return 1
  jq -e \
    --arg volume "$volume" \
    --arg compose_project "$RUNTIME_PREVIEW_COMPOSE_PROJECT" \
    --arg compose_volume "$compose_volume" \
    --arg project "$RUNTIME_PREVIEW_PROJECT" \
    --arg environment "$RUNTIME_PREVIEW_VOLUME_ENVIRONMENT" \
    --arg purpose "$purpose" '
      type == "array" and length == 1 and
      .[0].Name == $volume and .[0].Driver == "local" and .[0].Scope == "local" and
      (.[0].Options == null or .[0].Options == {}) and
      .[0].Labels["com.docker.compose.project"] == $compose_project and
      .[0].Labels["com.docker.compose.volume"] == $compose_volume and
      .[0].Labels["com.autonomia.project"] == $project and
      .[0].Labels["com.autonomia.issue"] == "77" and
      .[0].Labels["com.autonomia.environment"] == $environment and
      .[0].Labels["com.autonomia.purpose"] == $purpose and
      .[0].Labels["com.autonomia.source-sha"] == null and
      ((.[0].Labels | keys) - [
        "com.docker.compose.config-hash", "com.docker.compose.project", "com.docker.compose.version",
        "com.docker.compose.volume", "com.autonomia.project",
        "com.autonomia.issue", "com.autonomia.environment", "com.autonomia.purpose"
      ] | length == 0)
    ' <<<"$inspect" >/dev/null || {
    runtime_preview_fail "runtime-preview volume ownership drifted: $volume"
    return 1
  }
  case "$require_unused" in
    yes)
      consumers="$(docker ps -aq --filter "volume=$volume")" || {
        runtime_preview_fail "could not inventory runtime-preview volume consumers"
        return 1
      }
      [[ -z "$consumers" ]] || {
        runtime_preview_fail "runtime-preview volume has pre-existing container consumers: $volume"
        return 1
      }
      ;;
    no) ;;
    *) return 2 ;;
  esac
}

runtime_preview_ensure_owned_volume() {
  [[ $# -eq 3 ]] || return 2
  local volume="$1" compose_volume="$2" purpose="$3" inspect status created
  if inspect="$(runtime_preview_volume_inspect "$volume")"; then
    status=0
  else
    status=$?
  fi
  if [[ "$status" == "3" ]]; then
    created="$(docker volume create --driver local \
      --label "com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT" \
      --label "com.docker.compose.volume=$compose_volume" \
      --label "com.autonomia.project=$RUNTIME_PREVIEW_PROJECT" \
      --label 'com.autonomia.issue=77' \
      --label "com.autonomia.environment=$RUNTIME_PREVIEW_VOLUME_ENVIRONMENT" \
      --label "com.autonomia.purpose=$purpose" \
      "$volume")" || return 1
    [[ "$created" == "$volume" ]] || {
      runtime_preview_fail "Docker created an unexpected volume"
      return 1
    }
  elif [[ "$status" != "0" ]]; then
    return "$status"
  fi
  runtime_preview_verify_owned_volume "$volume" "$compose_volume" "$purpose" yes
}

runtime_preview_docker_config_path_is_safe() {
  [[ $# -eq 1 ]] || return 2
  [[ "$1" =~ ^[0-9a-f]{64}\.json$ || "$1" =~ ^blobs/sha256/[0-9a-f]{64}$ ]]
}

runtime_preview_docker_tar_has_one_regular_member() {
  [[ $# -eq 2 ]] || return 2
  local archive="$1" member="$2" listing
  [[ -f "$archive" && ! -L "$archive" ]] || return 1
  listing="$(tar -tvf "$archive" -- "$member" 2>/dev/null)" || return 1
  [[ "$listing" != *$'\n'* && "${listing:0:1}" == "-" ]]
}

runtime_preview_assert_run_root() {
  local candidate="$1" source_sha="$2" mode
  [[ "$source_sha" =~ ^[0-9a-f]{40}$ ]] || { runtime_preview_fail "invalid AppSell source SHA"; return 1; }
  [[ -d "$candidate" && ! -L "$candidate" ]] || { runtime_preview_fail "run root must be a non-symlink directory"; return 1; }
  RUNTIME_PREVIEW_RUN_ROOT="$(realpath -e "$candidate")" || return 1
  [[ "$RUNTIME_PREVIEW_RUN_ROOT" == "/opt/autonomia-sell-preview/issue-77/${source_sha:0:12}" ]] || {
    runtime_preview_fail "run root is outside the exact Issue #77 path"
    return 1
  }
  [[ "$(stat -c '%u:%g' "$RUNTIME_PREVIEW_RUN_ROOT")" == "0:0" ]] || { runtime_preview_fail "run root must be owned by root"; return 1; }
  mode="$(stat -c '%a' "$RUNTIME_PREVIEW_RUN_ROOT")" || return 1
  (( (8#$mode & 022) == 0 )) || { runtime_preview_fail "run root must not be group/world writable"; return 1; }
}

runtime_preview_assert_protected_directory() {
  local directory="$1" mode
  [[ -d "$directory" && ! -L "$directory" ]] || { runtime_preview_fail "protected path is not a regular directory"; return 1; }
  [[ "$(stat -c '%u:%g' "$directory")" == "0:0" ]] || { runtime_preview_fail "protected directory must be owned by root"; return 1; }
  mode="$(stat -c '%a' "$directory")" || return 1
  (( (8#$mode & 022) == 0 )) || { runtime_preview_fail "protected directory is group/world writable"; return 1; }
}

runtime_preview_assert_protected_file() {
  local file="$1" mode
  [[ -f "$file" && ! -L "$file" ]] || { runtime_preview_fail "protected path is not a regular file"; return 1; }
  [[ "$(stat -c '%u:%g:%h' "$file")" == "0:0:1" ]] || { runtime_preview_fail "protected file ownership/link count is invalid"; return 1; }
  mode="$(stat -c '%a' "$file")" || return 1
  (( (8#$mode & 022) == 0 )) || { runtime_preview_fail "protected file is group/world writable"; return 1; }
}

runtime_preview_assert_env_file() {
  local candidate="$1"
  [[ -f "$candidate" && ! -L "$candidate" ]] || { runtime_preview_fail "env file must be a regular non-symlink file"; return 1; }
  RUNTIME_PREVIEW_ENV_FILE="$(realpath -e "$candidate")" || return 1
  [[ "$(stat -c '%u:%g' "$RUNTIME_PREVIEW_ENV_FILE")" == "0:0" ]] || { runtime_preview_fail "env file must be owned by root"; return 1; }
  [[ "$(stat -c '%a' "$RUNTIME_PREVIEW_ENV_FILE")" == "600" ]] || { runtime_preview_fail "env file must use mode 0600"; return 1; }
  [[ "$(stat -c '%h' "$RUNTIME_PREVIEW_ENV_FILE")" == "1" ]] || { runtime_preview_fail "env file must have exactly one hard link"; return 1; }
}

runtime_preview_atomic_install_env() {
  local source="$1" destination="$2" parent temporary source_sha destination_sha
  runtime_preview_assert_protected_file "$source" || return 1
  [[ "$(stat -c '%a' "$source")" == "600" ]] || { runtime_preview_fail "source env must use mode 0600"; return 1; }
  parent="$(dirname "$destination")"
  runtime_preview_assert_protected_directory "$parent" || return 1
  source_sha="$(sha256sum "$source" | awk '{print $1}')" || return 1
  if [[ -e "$destination" || -L "$destination" ]]; then
    runtime_preview_assert_protected_file "$destination" || return 1
    [[ "$(stat -c '%a' "$destination")" == "600" ]] || { runtime_preview_fail "existing EasyPanel env must use mode 0600"; return 1; }
    destination_sha="$(sha256sum "$destination" | awk '{print $1}')" || return 1
    [[ "$destination_sha" == "$source_sha" ]] || { runtime_preview_fail "existing EasyPanel env does not match the attested env"; return 1; }
  fi
  temporary="$(mktemp "$parent/.env.XXXXXX")" || return 1
  if ! install -o root -g root -m 0600 "$source" "$temporary"; then
    rm -f -- "$temporary"
    return 1
  fi
  if ! mv -T -- "$temporary" "$destination"; then
    rm -f -- "$temporary"
    return 1
  fi
  [[ "$(stat -c '%a:%u:%g:%h' "$destination")" == "600:0:0:1" ]] || return 1
  [[ "$(sha256sum "$destination" | awk '{print $1}')" == "$source_sha" ]] || return 1
}

runtime_preview_remove_attested_env() {
  local source="$1" destination="$2" expected_sha actual_sha
  if [[ ! -e "$destination" && ! -L "$destination" ]]; then
    return 0
  fi
  runtime_preview_assert_protected_file "$source" || return 1
  runtime_preview_assert_protected_file "$destination" || return 1
  [[ "$(stat -c '%a' "$destination")" == "600" ]] || { runtime_preview_fail "EasyPanel env removal requires mode 0600"; return 1; }
  expected_sha="$(sha256sum "$source" | awk '{print $1}')" || return 1
  actual_sha="$(sha256sum "$destination" | awk '{print $1}')" || return 1
  [[ "$actual_sha" == "$expected_sha" ]] || { runtime_preview_fail "refusing to remove divergent EasyPanel env"; return 1; }
  rm -f -- "$destination" || return 1
}

runtime_preview_remove_easypanel_env() {
  local destination
  destination="/etc/easypanel/projects/$RUNTIME_PREVIEW_PROJECT/$RUNTIME_PREVIEW_SERVICE/code/.env"
  if [[ -e "$destination" || -L "$destination" ]]; then
    [[ "$(realpath -e "$(dirname "$destination")")" == "/etc/easypanel/projects/sell-autonomia/runtime-preview/code" ]] || return 1
  fi
  runtime_preview_remove_attested_env "$RUNTIME_PREVIEW_ENV_FILE" "$destination"
}

runtime_preview_env_value() {
  local key="$1" value
  value="$(awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; found++} END {if (found != 1) exit 3}' "$RUNTIME_PREVIEW_ENV_FILE")" || {
    runtime_preview_fail "missing or duplicate env key: $key"
    return 1
  }
  [[ -n "$value" && "$value" != *$'\n'* && "$value" != *$'\r'* ]] || { runtime_preview_fail "invalid env value: $key"; return 1; }
  printf '%s' "$value"
}

runtime_preview_api_login() {
  [[ "$RUNTIME_PREVIEW_API_BASE" == "http://127.0.0.1:3000" ]] || {
    runtime_preview_fail "EasyPanel API must be reached through VPS loopback"
    return 1
  }
  [[ -n "${EASYPANEL_EMAIL:-}" && -n "${EASYPANEL_PASSWORD:-}" ]] || {
    runtime_preview_fail "EASYPANEL_EMAIL and EASYPANEL_PASSWORD are required"
    return 1
  }
  local payload response
  payload="$(jq -cn --arg email "$EASYPANEL_EMAIL" --arg password "$EASYPANEL_PASSWORD" \
    '{json:{email:$email,password:$password,rememberMe:false}}')" || return 1
  response="$(printf '%s' "$payload" | curl -fsS --connect-timeout 5 --max-time 30 \
    -H 'content-type: application/json' --data-binary @- \
    "$RUNTIME_PREVIEW_API_BASE/api/rpc/auth/login")" || return 1
  RUNTIME_PREVIEW_API_TOKEN="$(jq -er '.json.token | select(type == "string" and test("^[A-Za-z0-9._-]{20,4096}$"))' <<<"$response")" || {
    runtime_preview_fail "EasyPanel login did not return a token"
    return 1
  }
  unset response payload
}

runtime_preview_api_post() {
  local endpoint="$1" payload="$2" auth_config response
  [[ "$endpoint" =~ ^/api/rpc/services/compose/[A-Za-z]+$ ]] || { runtime_preview_fail "unexpected EasyPanel endpoint"; return 1; }
  auth_config="$(mktemp)" || return 1
  chmod 0600 "$auth_config" || { rm -f -- "$auth_config"; return 1; }
  printf 'header = "authorization: Bearer %s"\n' "$RUNTIME_PREVIEW_API_TOKEN" >"$auth_config" || { rm -f -- "$auth_config"; return 1; }
  exec 9<"$auth_config" || { rm -f -- "$auth_config"; return 1; }
  rm -f -- "$auth_config" || { exec 9<&-; return 1; }
  if ! response="$(printf '%s' "$payload" | curl -fsS --connect-timeout 5 --max-time 180 \
    --config /dev/fd/9 -H 'content-type: application/json' \
    --data-binary @- "$RUNTIME_PREVIEW_API_BASE$endpoint")"; then
    exec 9<&-
    return 1
  fi
  exec 9<&-
  printf '%s' "$response"
}

runtime_preview_domain_api_post() {
  local endpoint="$1" payload="$2" auth_config response
  case "$endpoint" in
    /api/rpc/domains/listDomains|/api/rpc/domains/createDomain|/api/rpc/domains/setPrimaryDomain|/api/rpc/domains/deleteDomain) ;;
    *) runtime_preview_fail "unexpected EasyPanel domain endpoint"; return 1 ;;
  esac
  auth_config="$(mktemp)" || return 1
  chmod 0600 "$auth_config" || { rm -f -- "$auth_config"; return 1; }
  printf 'header = "authorization: Bearer %s"\n' "$RUNTIME_PREVIEW_API_TOKEN" >"$auth_config" || {
    rm -f -- "$auth_config"
    return 1
  }
  exec 9<"$auth_config" || { rm -f -- "$auth_config"; return 1; }
  rm -f -- "$auth_config" || { exec 9<&-; return 1; }
  if ! response="$(printf '%s' "$payload" | curl -fsS --connect-timeout 5 --max-time 30 \
    --config /dev/fd/9 -H 'content-type: application/json' \
    --data-binary @- "$RUNTIME_PREVIEW_API_BASE$endpoint")"; then
    exec 9<&-
    return 1
  fi
  exec 9<&-
  printf '%s' "$response"
}

runtime_preview_list_domains() {
  local payload
  if [[ "${1:-service}" == "service" ]]; then
    payload="$(jq -cn --arg projectName "$RUNTIME_PREVIEW_PROJECT" --arg serviceName "$RUNTIME_PREVIEW_SERVICE" \
      '{json:{projectName:$projectName,serviceName:$serviceName}}')" || return 1
  elif [[ "$1" == "all" ]]; then
    payload='{"json":{}}'
  else
    runtime_preview_fail "invalid EasyPanel domain list scope"
    return 1
  fi
  runtime_preview_domain_api_post /api/rpc/domains/listDomains "$payload"
}

runtime_preview_exact_public_domain_json() {
  local response count
  response="$(runtime_preview_list_domains service)" || return 1
  jq -e '.json | type == "array"' <<<"$response" >/dev/null || return 1
  count="$(jq -r '.json | length' <<<"$response")" || return 1
  [[ "$count" == "1" ]] || {
    [[ "$count" == "0" ]] && return 3
    runtime_preview_fail "runtime-preview must own exactly one EasyPanel domain"
    return 1
  }
  jq -ec \
    --arg host "$RUNTIME_PREVIEW_PUBLIC_HOST" \
    --arg project "$RUNTIME_PREVIEW_PROJECT" \
    --arg service "$RUNTIME_PREVIEW_SERVICE" '
      .json[0] |
      select(
        (.id | type == "string" and test("^[A-Za-z0-9._-]{8,128}$")) and
        .host == $host and .https == true and .path == "/" and
        .wildcard == false and .certificateResolver == "" and
        .middlewares == [] and .destinationType == "service" and
        .serviceDestination == {
          composeService:"sell-web", path:"/", port:13200,
          projectName:$project, protocol:"http", serviceName:$service
        }
      )
    ' <<<"$response" || {
      runtime_preview_fail "EasyPanel public domain configuration drifted"
      return 1
    }
}

runtime_preview_verify_public_domain() {
  local domain_json domain_id global_response global_domain_id host_count payload response
  domain_json="$(runtime_preview_exact_public_domain_json)" || return 1
  domain_id="$(jq -er '.id' <<<"$domain_json")" || return 1
  global_response="$(runtime_preview_list_domains all)" || return 1
  jq -e '.json | type == "array"' <<<"$global_response" >/dev/null || {
    runtime_preview_fail "EasyPanel global domain list has an invalid type"
    return 1
  }
  host_count="$(jq -r --arg host "$RUNTIME_PREVIEW_PUBLIC_HOST" '[.json[] | select(.host == $host)] | length' \
    <<<"$global_response")" || return 1
  [[ "$host_count" == "1" ]] || {
    runtime_preview_fail "public host is missing or duplicated in EasyPanel"
    return 1
  }
  global_domain_id="$(jq -er --arg host "$RUNTIME_PREVIEW_PUBLIC_HOST" \
    '.json[] | select(.host == $host) | .id' <<<"$global_response")" || return 1
  [[ "$global_domain_id" == "$domain_id" ]] || {
    runtime_preview_fail "global public host belongs to a different EasyPanel domain"
    return 1
  }
  payload="$(jq -cn --arg projectName "$RUNTIME_PREVIEW_PROJECT" --arg serviceName "$RUNTIME_PREVIEW_SERVICE" \
    '{json:{projectName:$projectName,serviceName:$serviceName}}')" || return 1
  response="$(runtime_preview_api_inspect "$payload")" || return 1
  [[ "$(jq -er '.json.primaryDomainId' <<<"$response")" == "$domain_id" ]] || {
    runtime_preview_fail "EasyPanel primary domain does not match the exact public domain"
    return 1
  }
  printf '%s' "$domain_id"
}

runtime_preview_verify_public_domain_absent() {
  local service_response global_response payload inspect_response
  service_response="$(runtime_preview_list_domains service)" || return 1
  global_response="$(runtime_preview_list_domains all)" || return 1
  jq -e '.json | type == "array"' <<<"$service_response" >/dev/null || {
    runtime_preview_fail "EasyPanel service domain list has an invalid type"
    return 1
  }
  jq -e '.json | type == "array"' <<<"$global_response" >/dev/null || {
    runtime_preview_fail "EasyPanel global domain list has an invalid type"
    return 1
  }
  [[ "$(jq -er '.json | length' <<<"$service_response")" == "0" ]] || {
    runtime_preview_fail "runtime-preview still owns an EasyPanel domain"
    return 1
  }
  [[ "$(jq -r --arg host "$RUNTIME_PREVIEW_PUBLIC_HOST" \
    '[.json[] | select(.host == $host)] | length' <<<"$global_response")" == "0" ]] || {
    runtime_preview_fail "public host still exists in EasyPanel"
    return 1
  }
  payload="$(jq -cn --arg projectName "$RUNTIME_PREVIEW_PROJECT" --arg serviceName "$RUNTIME_PREVIEW_SERVICE" \
    '{json:{projectName:$projectName,serviceName:$serviceName}}')" || return 1
  inspect_response="$(runtime_preview_api_inspect "$payload")" || return 1
  jq -e '.json.primaryDomainId == null or .json.primaryDomainId == ""' \
    <<<"$inspect_response" >/dev/null || {
    runtime_preview_fail "runtime-preview primary domain was not cleared"
    return 1
  }
}

runtime_preview_delete_exact_public_domain() {
  local domain_json domain_id payload response
  domain_json="$(runtime_preview_exact_public_domain_json)" || return 1
  domain_id="$(jq -er '.id' <<<"$domain_json")" || return 1
  payload="$(jq -cn --arg id "$domain_id" '{json:{id:$id}}')" || return 1
  if ! response="$(runtime_preview_domain_api_post /api/rpc/domains/deleteDomain "$payload")"; then
    runtime_preview_verify_public_domain_absent && return 0
    return 1
  fi
  if ! runtime_preview_expect_api_success "$response" deleteDomain; then
    runtime_preview_verify_public_domain_absent && return 0
    return 1
  fi
  runtime_preview_verify_public_domain_absent
}

runtime_preview_verify_public_attestation() {
  local attestation domain_id certificate certificate_pem certificate_sha256 headers
  attestation="$RUNTIME_PREVIEW_RUN_ROOT/public-domain.attestation.json"
  runtime_preview_assert_protected_file "$attestation" || return 1
  runtime_preview_verify_public_dns || return 1
  domain_id="$(runtime_preview_verify_public_domain)" || return 1
  headers="$(mktemp)" || return 1
  if ! curl -fsS --connect-timeout 5 --max-time 20 -D "$headers" -o /dev/null \
    "$RUNTIME_PREVIEW_PUBLIC_ORIGIN/" ||
    ! grep -Eiq '^strict-transport-security:[[:space:]]*max-age=31536000;[[:space:]]*includeSubDomains[[:space:]]*$' \
      "$headers"; then
    rm -f -- "$headers"
    runtime_preview_fail "public HTTPS trust or HSTS verification failed"
    return 1
  fi
  rm -f -- "$headers"
  certificate="$(mktemp)" || return 1
  if ! certificate_pem="$(timeout 15 openssl s_client \
    -connect "$RUNTIME_PREVIEW_PUBLIC_HOST:443" \
    -servername "$RUNTIME_PREVIEW_PUBLIC_HOST" </dev/null 2>/dev/null)"; then
    rm -f -- "$certificate"
    runtime_preview_fail "could not read the public TLS certificate"
    return 1
  fi
  if ! printf '%s' "$certificate_pem" | openssl x509 -out "$certificate"; then
    rm -f -- "$certificate"
    runtime_preview_fail "public TLS certificate parsing failed"
    return 1
  fi
  unset certificate_pem
  openssl x509 -in "$certificate" -noout -checkhost "$RUNTIME_PREVIEW_PUBLIC_HOST" >/dev/null || {
    rm -f -- "$certificate"
    return 1
  }
  openssl x509 -in "$certificate" -noout -checkend 604800 >/dev/null || {
    rm -f -- "$certificate"
    return 1
  }
  certificate_sha256="$(openssl x509 -in "$certificate" -outform DER | sha256sum | awk '{print $1}')" || {
    rm -f -- "$certificate"
    return 1
  }
  rm -f -- "$certificate"
  jq -e \
    --arg project "$RUNTIME_PREVIEW_PROJECT" --arg service "$RUNTIME_PREVIEW_SERVICE" \
    --arg source_sha "$(runtime_preview_env_value APPSELL_SOURCE_SHA)" \
    --arg domain_id "$domain_id" --arg host "$RUNTIME_PREVIEW_PUBLIC_HOST" \
    --arg origin "$RUNTIME_PREVIEW_PUBLIC_ORIGIN" --arg ipv4 "$RUNTIME_PREVIEW_PUBLIC_IPV4" \
    --arg certificate_sha256 "$certificate_sha256" '
      .schema_version == "autonomia.easypanel-public-domain.v1" and
      .project == $project and .service == $service and .source_sha == $source_sha and
      .domain_id == $domain_id and .host == $host and .origin == $origin and
      .ipv4 == $ipv4 and .certificate_sha256 == $certificate_sha256 and
      (.checked_at | type == "string" and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"))
    ' "$attestation" >/dev/null || {
    runtime_preview_fail "public-domain attestation mismatch"
    return 1
  }
}

runtime_preview_api_inspect() {
  local payload="$1" auth_config body_file status response
  auth_config="$(mktemp)" || return 1
  chmod 0600 "$auth_config" || { rm -f -- "$auth_config"; return 1; }
  printf 'header = "authorization: Bearer %s"\n' "$RUNTIME_PREVIEW_API_TOKEN" >"$auth_config" || { rm -f -- "$auth_config"; return 1; }
  exec 9<"$auth_config" || { rm -f -- "$auth_config"; return 1; }
  rm -f -- "$auth_config" || { exec 9<&-; return 1; }
  body_file="$(mktemp)" || { exec 9<&-; return 1; }
  status="$(printf '%s' "$payload" | curl -sS --connect-timeout 5 --max-time 30 \
    -o "$body_file" -w '%{http_code}' \
    --config /dev/fd/9 -H 'content-type: application/json' \
    --data-binary @- \
    "$RUNTIME_PREVIEW_API_BASE/api/rpc/services/compose/inspectService")" || {
      exec 9<&-
      rm -f -- "$body_file"
      return 1
    }
  exec 9<&-
  response="$(<"$body_file")" || { rm -f -- "$body_file"; return 1; }
  rm -f -- "$body_file" || return 1
  if [[ "$status" == "200" ]]; then
    jq -e '.json | type == "object" and (.code? == null)' <<<"$response" >/dev/null || {
      runtime_preview_fail "EasyPanel returned an invalid successful inspect response"
      return 1
    }
  elif [[ "$status" == "404" ]]; then
    jq -e '.json.code == "NOT_FOUND" and .json.status == 404' <<<"$response" >/dev/null || {
      runtime_preview_fail "EasyPanel returned an unexpected 404 inspect response"
      return 1
    }
  else
    runtime_preview_fail "EasyPanel inspect returned HTTP $status"
    return 1
  fi
  printf '%s' "$response"
}

runtime_preview_service_state() {
  local payload response code
  payload="$(jq -cn --arg projectName "$RUNTIME_PREVIEW_PROJECT" --arg serviceName "$RUNTIME_PREVIEW_SERVICE" \
    '{json:{projectName:$projectName,serviceName:$serviceName}}')" || return 1
  response="$(runtime_preview_api_inspect "$payload")" || return 1
  code="$(jq -r '.json.code // empty' <<<"$response")" || return 1
  if [[ "$code" == "NOT_FOUND" ]]; then
    printf 'absent'
  elif [[ -z "$code" && "$(jq -r '.json | type' <<<"$response")" == "object" ]]; then
    printf 'present'
  else
    runtime_preview_fail "EasyPanel returned an unexpected inspect response"
    return 1
  fi
}

runtime_preview_created_record_token_fingerprint() {
  local response="$1"
  jq -e \
    --arg project "$RUNTIME_PREVIEW_PROJECT" --arg service "$RUNTIME_PREVIEW_SERVICE" '
      .json.projectName == $project and .json.name == $service and
      .json.type == "compose" and .json.createDotEnv == false and
      (.json.primaryDomainId == null or .json.primaryDomainId == "") and
      .json.env == "" and
      (.json.token | type == "string" and test("^[A-Za-z0-9._-]{20,4096}$"))
    ' <<<"$response" >/dev/null || {
      runtime_preview_fail "createService did not return the exact new runtime-preview record"
      return 1
    }
  jq -jer '.json.token' <<<"$response" | sha256sum | awk '{print $1}' || return 1
}

runtime_preview_expect_api_success() {
  local response="$1" operation="$2"
  jq -e '(.error? == null) and ((.json | type) != "object" or (.json.code? == null))' <<<"$response" >/dev/null || {
    runtime_preview_fail "EasyPanel $operation failed"
    return 1
  }
}

runtime_preview_snapshot_services() {
  local output="$1" name digest service_names
  [[ ! -e "$output" && ! -L "$output" ]] || { runtime_preview_fail "service snapshot collision"; return 1; }
  install -o root -g root -m 0600 /dev/null "$output" || return 1
  service_names="$(docker service ls --format '{{.Name}}')" || return 1
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    digest="$(docker service inspect "$name" | jq -S '.[0].Spec' | sha256sum | awk '{print $1}')" || return 1
    printf '%s|%s\n' "$name" "$digest" >>"$output" || return 1
  done < <(LC_ALL=C sort <<<"$service_names")
}

runtime_preview_verify_service_snapshot() {
  local snapshot="$1" current name digest service_names
  [[ -f "$snapshot" && ! -L "$snapshot" && "$(stat -c '%a' "$snapshot")" == "600" ]] || {
    runtime_preview_fail "invalid protected-service snapshot"
    return 1
  }
  current="$(mktemp)"
  trap 'rm -f -- "$current"' RETURN
  service_names="$(docker service ls --format '{{.Name}}')" || { rm -f -- "$current"; trap - RETURN; return 1; }
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    digest="$(docker service inspect "$name" | jq -S '.[0].Spec' | sha256sum | awk '{print $1}')" || { rm -f -- "$current"; trap - RETURN; return 1; }
    printf '%s|%s\n' "$name" "$digest" >>"$current" || { rm -f -- "$current"; trap - RETURN; return 1; }
  done < <(LC_ALL=C sort <<<"$service_names")
  cmp -s "$snapshot" "$current" || { runtime_preview_fail "a pre-existing Docker service specification changed"; rm -f -- "$current"; trap - RETURN; return 1; }
  rm -f -- "$current"
  trap - RETURN
}

runtime_preview_write_attestation() {
  local state="$1" token_fingerprint="${2:-}" compose_file attestation temporary compose_sha env_sha source_sha
  case "$state" in intent|provisioning|configured|active|stopped|rollback_failed) ;; *) runtime_preview_fail "invalid attestation state"; return 1 ;; esac
  if [[ "$state" == "intent" ]]; then
    [[ -z "$token_fingerprint" ]] || { runtime_preview_fail "intent attestation cannot contain a token fingerprint"; return 1; }
  else
    [[ "$token_fingerprint" =~ ^[0-9a-f]{64}$ ]] || { runtime_preview_fail "invalid EasyPanel token fingerprint"; return 1; }
  fi
  compose_file="$RUNTIME_PREVIEW_RUN_ROOT/config/easypanel/compose.runtime-preview.yaml"
  runtime_preview_assert_protected_file "$compose_file" || return 1
  compose_sha="$(sha256sum "$compose_file" | awk '{print $1}')" || return 1
  env_sha="$(sha256sum "$RUNTIME_PREVIEW_ENV_FILE" | awk '{print $1}')" || return 1
  source_sha="$(runtime_preview_env_value APPSELL_SOURCE_SHA)" || return 1
  attestation="$RUNTIME_PREVIEW_RUN_ROOT/easypanel-runtime-preview.attestation.json"
  temporary="$(mktemp "$RUNTIME_PREVIEW_RUN_ROOT/.attestation.XXXXXX")" || return 1
  chmod 0600 "$temporary" || { rm -f -- "$temporary"; return 1; }
  jq -n \
    --arg schema_version autonomia.easypanel-runtime-preview.v1 \
    --arg project "$RUNTIME_PREVIEW_PROJECT" --arg service "$RUNTIME_PREVIEW_SERVICE" \
    --arg issue 77 --arg source_sha "$source_sha" \
    --arg compose_sha256 "$compose_sha" --arg env_sha256 "$env_sha" \
    --arg token_sha256 "$token_fingerprint" --arg state "$state" \
    '{schema_version:$schema_version,project:$project,service:$service,issue:$issue,
      source_sha:$source_sha,compose_sha256:$compose_sha256,env_sha256:$env_sha256,
      token_sha256:$token_sha256,state:$state}' \
    >"$temporary" || { rm -f -- "$temporary"; return 1; }
  chown root:root "$temporary" || { rm -f -- "$temporary"; return 1; }
  mv -f -- "$temporary" "$attestation" || { rm -f -- "$temporary"; return 1; }
  chmod 0600 "$attestation" || return 1
  [[ "$(stat -c '%a:%u:%g:%h' "$attestation")" == "600:0:0:1" ]] || { runtime_preview_fail "written attestation permissions are invalid"; return 1; }
}

runtime_preview_verify_attestation() {
  local allowed_states="$1" attestation compose_file compose_sha env_sha source_sha
  attestation="$RUNTIME_PREVIEW_RUN_ROOT/easypanel-runtime-preview.attestation.json"
  compose_file="$RUNTIME_PREVIEW_RUN_ROOT/config/easypanel/compose.runtime-preview.yaml"
  [[ -f "$attestation" && ! -L "$attestation" ]] || { runtime_preview_fail "runtime-preview ownership attestation is missing"; return 1; }
  [[ "$(stat -c '%a:%u:%g:%h' "$attestation")" == "600:0:0:1" ]] || { runtime_preview_fail "runtime-preview attestation permissions are invalid"; return 1; }
  runtime_preview_assert_protected_file "$compose_file" || return 1
  compose_sha="$(sha256sum "$compose_file" | awk '{print $1}')" || return 1
  env_sha="$(sha256sum "$RUNTIME_PREVIEW_ENV_FILE" | awk '{print $1}')" || return 1
  source_sha="$(runtime_preview_env_value APPSELL_SOURCE_SHA)" || return 1
  jq -e \
    --arg project "$RUNTIME_PREVIEW_PROJECT" --arg service "$RUNTIME_PREVIEW_SERVICE" \
    --arg source_sha "$source_sha" \
    --arg compose_sha256 "$compose_sha" --arg env_sha256 "$env_sha" \
    --arg allowed_states "$allowed_states" '
      .schema_version == "autonomia.easypanel-runtime-preview.v1" and
      .project == $project and .service == $service and .issue == "77" and
      .source_sha == $source_sha and .compose_sha256 == $compose_sha256 and
      .env_sha256 == $env_sha256 and
      ((.state == "intent" and .token_sha256 == "") or
       (.state != "intent" and (.token_sha256 | test("^[0-9a-f]{64}$")))) and
      (.state as $state | ($allowed_states | split("|") | index($state)) != null)
    ' "$attestation" >/dev/null || { runtime_preview_fail "runtime-preview ownership attestation mismatch"; return 1; }
}

runtime_preview_attestation_value() {
  local key="$1" attestation
  case "$key" in state|token_sha256) ;; *) runtime_preview_fail "unsupported attestation field"; return 1 ;; esac
  attestation="$RUNTIME_PREVIEW_RUN_ROOT/easypanel-runtime-preview.attestation.json"
  [[ -f "$attestation" && ! -L "$attestation" && "$(stat -c '%a:%u:%g:%h' "$attestation")" == "600:0:0:1" ]] || return 1
  jq -er --arg key "$key" '.[$key] | select(type == "string")' "$attestation"
}

runtime_preview_verify_live_record() {
  local require_inline="$1" payload response token_fingerprint expected_token compose_file compose_sha source_sha code_file primary_domain_id verified_domain_id
  payload="$(jq -cn --arg projectName "$RUNTIME_PREVIEW_PROJECT" --arg serviceName "$RUNTIME_PREVIEW_SERVICE" \
    '{json:{projectName:$projectName,serviceName:$serviceName}}')"
  response="$(runtime_preview_api_inspect "$payload")" || return 1
  jq -e \
    --arg project "$RUNTIME_PREVIEW_PROJECT" --arg service "$RUNTIME_PREVIEW_SERVICE" '
      .json.projectName == $project and .json.name == $service and
      .json.type == "compose" and .json.createDotEnv == false and
      (.json.primaryDomainId == null or .json.primaryDomainId == "" or
       (.json.primaryDomainId | type == "string" and test("^[A-Za-z0-9._-]{8,128}$"))) and
      .json.env == ""
    ' <<<"$response" >/dev/null || { runtime_preview_fail "EasyPanel runtime-preview record metadata mismatch"; return 1; }
  primary_domain_id="$(jq -r '.json.primaryDomainId // empty' <<<"$response")" || return 1
  if [[ -n "$primary_domain_id" ]]; then
    verified_domain_id="$(runtime_preview_verify_public_domain)" || return 1
    [[ "$verified_domain_id" == "$primary_domain_id" ]] || {
      runtime_preview_fail "EasyPanel live record references an unowned primary domain"
      return 1
    }
  fi
  expected_token="$(runtime_preview_attestation_value token_sha256)" || return 1
  token_fingerprint="$(jq -jer '.json.token' <<<"$response" | sha256sum | awk '{print $1}')" || return 1
  [[ "$token_fingerprint" == "$expected_token" ]] || { runtime_preview_fail "EasyPanel runtime-preview token fingerprint mismatch"; return 1; }
  if [[ "$require_inline" == "yes" ]]; then
    jq -e '.json.source.type == "inline" and (.json.source.content | type == "string")' <<<"$response" >/dev/null || {
      runtime_preview_fail "EasyPanel runtime-preview source is not inline"
      return 1
    }
    compose_file="$RUNTIME_PREVIEW_RUN_ROOT/config/easypanel/compose.runtime-preview.yaml"
    runtime_preview_assert_protected_file "$compose_file" || return 1
    compose_sha="$(sha256sum "$compose_file" | awk '{print $1}')" || return 1
    source_sha="$(jq -jer '.json.source.content' <<<"$response" | sha256sum | awk '{print $1}')" || return 1
    [[ "$source_sha" == "$compose_sha" ]] || { runtime_preview_fail "EasyPanel inline Compose hash mismatch"; return 1; }
    code_file="/etc/easypanel/projects/$RUNTIME_PREVIEW_PROJECT/$RUNTIME_PREVIEW_SERVICE/code/docker-compose.yml"
    if [[ -e "$code_file" || -L "$code_file" ]]; then
      runtime_preview_assert_protected_file "$code_file" || return 1
      [[ "$(sha256sum "$code_file" | awk '{print $1}')" == "$compose_sha" ]] || { runtime_preview_fail "EasyPanel Compose file hash mismatch"; return 1; }
    fi
  elif [[ "$require_inline" != "no" ]]; then
    runtime_preview_fail "invalid live-record verification mode"
    return 1
  fi
}

runtime_preview_compose_down() {
  local compose_file source_sha id full_id service runtime_output remaining_output ingress_json network_names owned_ingress_json
  compose_file="$RUNTIME_PREVIEW_RUN_ROOT/config/easypanel/compose.runtime-preview.yaml"
  runtime_preview_assert_protected_file "$compose_file" || return 1
  source_sha="$(runtime_preview_env_value APPSELL_SOURCE_SHA)" || return 1
  declare -A seen_services=()
  declare -A owned_ingress_ids=()
  runtime_output="$(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT")" || {
    runtime_preview_fail "could not inventory runtime-preview containers before teardown"
    return 1
  }
  runtime_ids=()
  if [[ -n "$runtime_output" ]]; then
    mapfile -t runtime_ids <<<"$runtime_output"
  fi
  for id in "${runtime_ids[@]}"; do
    [[ "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$id")" == "$RUNTIME_PREVIEW_COMPOSE_PROJECT" ]] || return 1
    [[ "$(docker inspect --format '{{index .Config.Labels "com.autonomia.project"}}' "$id")" == "$RUNTIME_PREVIEW_PROJECT" ]] || return 1
    [[ "$(docker inspect --format '{{index .Config.Labels "com.autonomia.issue"}}' "$id")" == "77" ]] || return 1
    [[ "$(docker inspect --format '{{index .Config.Labels "com.autonomia.source-sha"}}' "$id")" == "$source_sha" ]] || return 1
    service="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$id")"
    case "$service" in domain-bootstrap|domain-api|sell-web|ingress-proxy) ;; *) runtime_preview_fail "unexpected service in runtime-preview teardown"; return 1 ;; esac
    [[ -z "${seen_services[$service]:-}" ]] || { runtime_preview_fail "duplicate service in runtime-preview teardown"; return 1; }
    seen_services[$service]=1
    if [[ "$service" == "sell-web" || "$service" == "ingress-proxy" ]]; then
      full_id="$(docker inspect --format '{{.Id}}' "$id")" || return 1
      [[ "$full_id" =~ ^[0-9a-f]{64}$ ]] || return 1
      owned_ingress_ids[$full_id]=1
    fi
  done
  owned_ingress_json="$(printf '%s\n' "${!owned_ingress_ids[@]}" | sed '/^$/d' | jq -Rsc 'split("\n") | map(select(length > 0))')" || return 1
  if ingress_json="$(docker network inspect "$RUNTIME_PREVIEW_INGRESS_NETWORK" 2>/dev/null)"; then
    jq -e \
      --arg network "$RUNTIME_PREVIEW_INGRESS_NETWORK" --arg compose_project "$RUNTIME_PREVIEW_COMPOSE_PROJECT" \
      --arg source_sha "$source_sha" --argjson owned_ids "$owned_ingress_json" '
        type == "array" and length == 1 and .[0].Name == $network and .[0].Internal == true and
        .[0].Labels["com.docker.compose.project"] == $compose_project and
        .[0].Labels["com.docker.compose.network"] == "sell-ingress" and
        .[0].Labels["com.autonomia.project"] == "sell-autonomia" and
        .[0].Labels["com.autonomia.issue"] == "77" and
        .[0].Labels["com.autonomia.purpose"] == "ingress-proxy" and
        .[0].Labels["com.autonomia.source-sha"] == $source_sha and
        ((.[0].Containers | keys) - $owned_ids | length) == 0
      ' <<<"$ingress_json" >/dev/null || {
      runtime_preview_fail "refusing teardown with an untrusted ingress network"
      return 1
    }
  else
    network_names="$(docker network ls --format '{{.Name}}')" || return 1
    ! grep -Fxq "$RUNTIME_PREVIEW_INGRESS_NETWORK" <<<"$network_names" || {
      runtime_preview_fail "ingress network exists but could not be inspected"
      return 1
    }
  fi
  unset COMPOSE_PROJECT_NAME COMPOSE_FILE COMPOSE_PROFILES COMPOSE_ENV_FILES
  docker compose --project-name "$RUNTIME_PREVIEW_COMPOSE_PROJECT" \
    --env-file "$RUNTIME_PREVIEW_ENV_FILE" -f "$compose_file" \
    down --remove-orphans || { runtime_preview_fail "exact runtime-preview Compose down failed"; return 1; }
  remaining_output="$(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT")" || {
    runtime_preview_fail "could not inventory runtime-preview containers after teardown"
    return 1
  }
  [[ -z "$remaining_output" ]] || {
    runtime_preview_fail "runtime-preview containers remain after exact Compose down"
    return 1
  }
  network_names="$(docker network ls --format '{{.Name}}')" || return 1
  ! grep -Fxq "$RUNTIME_PREVIEW_INGRESS_NETWORK" <<<"$network_names" || {
    runtime_preview_fail "runtime-preview ingress network remained after Compose down"
    return 1
  }
}
