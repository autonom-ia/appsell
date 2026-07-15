#!/usr/bin/env bash

RUNTIME_PREVIEW_PROJECT="sell-autonomia"
RUNTIME_PREVIEW_SERVICE="runtime-preview"
RUNTIME_PREVIEW_COMPOSE_PROJECT="sell-autonomia_runtime-preview"
RUNTIME_PREVIEW_NETWORK="easypanel-sell-autonomia"
RUNTIME_PREVIEW_API_BASE="${EASYPANEL_API_BASE:-http://127.0.0.1:3000}"

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
  local require_inline="$1" payload response token_fingerprint expected_token compose_file compose_sha source_sha code_file
  payload="$(jq -cn --arg projectName "$RUNTIME_PREVIEW_PROJECT" --arg serviceName "$RUNTIME_PREVIEW_SERVICE" \
    '{json:{projectName:$projectName,serviceName:$serviceName}}')"
  response="$(runtime_preview_api_inspect "$payload")" || return 1
  jq -e \
    --arg project "$RUNTIME_PREVIEW_PROJECT" --arg service "$RUNTIME_PREVIEW_SERVICE" '
      .json.projectName == $project and .json.name == $service and
      .json.type == "compose" and .json.createDotEnv == false and
      (.json.primaryDomainId == null or .json.primaryDomainId == "") and
      .json.env == ""
    ' <<<"$response" >/dev/null || { runtime_preview_fail "EasyPanel runtime-preview record metadata mismatch"; return 1; }
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
  local compose_file source_sha id service runtime_output remaining_output
  compose_file="$RUNTIME_PREVIEW_RUN_ROOT/config/easypanel/compose.runtime-preview.yaml"
  runtime_preview_assert_protected_file "$compose_file" || return 1
  source_sha="$(runtime_preview_env_value APPSELL_SOURCE_SHA)" || return 1
  declare -A seen_services=()
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
    case "$service" in domain-bootstrap|domain-api|sell-web) ;; *) runtime_preview_fail "unexpected service in runtime-preview teardown"; return 1 ;; esac
    [[ -z "${seen_services[$service]:-}" ]] || { runtime_preview_fail "duplicate service in runtime-preview teardown"; return 1; }
    seen_services[$service]=1
  done
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
}
