#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-preview-common.sh
source "$here/runtime-preview-common.sh"
RUNTIME_PREVIEW_API_TOKEN=fixture-token-long-enough-for-tests
mock_mode=absent

digest=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

exact_network_fixture='[{"NetworkSettings":{"Networks":{"easypanel-sell-autonomia":{}}}}]'
runtime_preview_inspect_has_exact_network "$exact_network_fixture" "easypanel-sell-autonomia"
for rejected_network_fixture in \
  '[{"NetworkSettings":{"Networks":{}}}]' \
  '[{"NetworkSettings":{"Networks":{"bridge":{}}}}]' \
  '[{"NetworkSettings":{"Networks":{"easypanel-sell-autonomia":{},"bridge":{}}}}]' \
  '[{"NetworkSettings":{"Networks":{"easypanel-sell-autonomia":{}}}},{"NetworkSettings":{"Networks":{"easypanel-sell-autonomia":{}}}}]'; do
  if runtime_preview_inspect_has_exact_network "$rejected_network_fixture" "easypanel-sell-autonomia"; then
    echo "invalid runtime-preview network inspection was accepted" >&2
    exit 1
  fi
done

lifecycle_fixture='[{"Id":"container-a","Config":{"Labels":{"com.docker.compose.service":"sell-web"}},"State":{"StartedAt":"2026-07-16T12:00:00Z","FinishedAt":"0001-01-01T00:00:00Z","ExitCode":0},"RestartCount":0}]'
baseline_fingerprint="$(runtime_preview_lifecycle_fingerprint_from_inspect "$lifecycle_fixture")"
[[ "$baseline_fingerprint" == 'sell-web|container-a|2026-07-16T12:00:00Z|0001-01-01T00:00:00Z|0|0' ]]
for mutation in \
  '.[0].Config.Labels["com.docker.compose.service"]="domain-api"' \
  '.[0].Id="container-b"' \
  '.[0].State.StartedAt="2026-07-16T12:01:00Z"' \
  '.[0].State.FinishedAt="2026-07-16T12:02:00Z"' \
  '.[0].RestartCount=1' \
  '.[0].State.ExitCode=1'; do
  mutated_fixture="$(jq -c "$mutation" <<<"$lifecycle_fixture")"
  mutated_fingerprint="$(runtime_preview_lifecycle_fingerprint_from_inspect "$mutated_fixture")"
  [[ "$mutated_fingerprint" != "$baseline_fingerprint" ]] || {
    echo "runtime-preview lifecycle mutation was not detected: $mutation" >&2
    exit 1
  }
done
for rejected_lifecycle_fixture in \
  '[]' \
  '[{},{}]' \
  '[{"Id":"container-a","Config":{"Labels":{"com.docker.compose.service":"e2e"}},"State":{"StartedAt":"x","FinishedAt":"y","ExitCode":0},"RestartCount":0}]'; do
  if runtime_preview_lifecycle_fingerprint_from_inspect "$rejected_lifecycle_fixture" >/dev/null 2>&1; then
    echo "invalid runtime-preview lifecycle inspection was accepted" >&2
    exit 1
  fi
done

runtime_preview_docker_config_path_is_safe "$digest.json"
runtime_preview_docker_config_path_is_safe "blobs/sha256/$digest"
for rejected in \
  "blobs/sha256/$digest.json" \
  "../$digest.json" \
  "/blobs/sha256/$digest" \
  "blobs/sha256/${digest}0" \
  "sha256:$digest"; do
  if runtime_preview_docker_config_path_is_safe "$rejected"; then
    echo "unsafe Docker image config path was accepted: $rejected" >&2
    exit 1
  fi
done

archive_tmp="$(mktemp -d)"
trap 'rm -rf -- "$archive_tmp"' EXIT
mkdir -p "$archive_tmp/valid/blobs/sha256"
printf '[]\n' >"$archive_tmp/valid/manifest.json"
printf '{}\n' >"$archive_tmp/valid/blobs/sha256/$digest"
tar -cf "$archive_tmp/valid.tar" -C "$archive_tmp/valid" manifest.json "blobs/sha256/$digest"
runtime_preview_docker_tar_has_one_regular_member "$archive_tmp/valid.tar" manifest.json
runtime_preview_docker_tar_has_one_regular_member "$archive_tmp/valid.tar" "blobs/sha256/$digest"

cp "$archive_tmp/valid.tar" "$archive_tmp/duplicate.tar"
tar -rf "$archive_tmp/duplicate.tar" -C "$archive_tmp/valid" "blobs/sha256/$digest"
if runtime_preview_docker_tar_has_one_regular_member "$archive_tmp/duplicate.tar" "blobs/sha256/$digest"; then
  echo "duplicate Docker TAR config member was accepted" >&2
  exit 1
fi

mkdir -p "$archive_tmp/symlink/blobs/sha256"
ln -s ../../manifest.json "$archive_tmp/symlink/blobs/sha256/$digest"
printf '[]\n' >"$archive_tmp/symlink/manifest.json"
tar -cf "$archive_tmp/symlink.tar" -C "$archive_tmp/symlink" manifest.json "blobs/sha256/$digest"
if runtime_preview_docker_tar_has_one_regular_member "$archive_tmp/symlink.tar" "blobs/sha256/$digest"; then
  echo "symlink Docker TAR config member was accepted" >&2
  exit 1
fi

curl() {
  local output_file="" argument
  while [[ $# -gt 0 ]]; do
    argument="$1"
    shift
    if [[ "$argument" == "-o" ]]; then
      output_file="$1"
      shift
    fi
  done
  [[ -n "$output_file" ]] || return 2
  case "$mock_mode" in
    absent)
      printf '%s' '{"json":{"code":"NOT_FOUND","status":404,"defined":false,"message":"Service not found."}}' >"$output_file"
      printf '404'
      ;;
    present)
      printf '%s' '{"json":{"name":"runtime-preview","projectName":"sell-autonomia"}}' >"$output_file"
      printf '200'
      ;;
    invalid-404)
      printf '%s' '{"json":{"code":"NOT_FOUND","status":403}}' >"$output_file"
      printf '404'
      ;;
    *) return 2 ;;
  esac
}

[[ "$(runtime_preview_service_state)" == "absent" ]]
mock_mode=present
[[ "$(runtime_preview_service_state)" == "present" ]]
mock_mode=invalid-404
if runtime_preview_service_state >/dev/null 2>&1; then
  echo "invalid inspect 404 was accepted" >&2
  exit 1
fi
runtime_preview_expect_api_success '{"json":true}' fixture-success
created='{"json":{"projectName":"sell-autonomia","name":"runtime-preview","type":"compose","createDotEnv":false,"primaryDomainId":null,"env":"","token":"abcdefghijklmnopqrstuvwxyzABCDEFGH0123456789_-x"}}'
created_fingerprint="$(runtime_preview_created_record_token_fingerprint "$created")"
[[ "$created_fingerprint" == "$(printf '%s' 'abcdefghijklmnopqrstuvwxyzABCDEFGH0123456789_-x' | sha256sum | awk '{print $1}')" ]]
if runtime_preview_created_record_token_fingerprint \
  '{"json":{"projectName":"sell-autonomia","name":"runtime-preview","type":"compose","createDotEnv":false,"primaryDomainId":null,"env":"","token":"attacker"}}' \
  >/dev/null 2>&1; then
  echo "invalid createService ownership response was accepted" >&2
  exit 1
fi
if runtime_preview_expect_api_success '{"json":{"code":"BAD_REQUEST"}}' fixture-failure >/dev/null 2>&1; then
  echo "API application error was accepted" >&2
  exit 1
fi

volume_name="$RUNTIME_PREVIEW_E2E_TRANSPORT_VOLUME"
volume_fixture="$(jq -cn \
  --arg name "$volume_name" \
  --arg project "$RUNTIME_PREVIEW_COMPOSE_PROJECT" '
  [{Name:$name,Driver:"local",Scope:"local",Options:null,Labels:{
    "com.docker.compose.project":$project,
    "com.docker.compose.volume":"runtime_e2e_transport",
    "com.autonomia.project":"sell-autonomia",
    "com.autonomia.issue":"77",
    "com.autonomia.environment":"local-ci-easypanel-preview",
    "com.autonomia.purpose":"runtime-e2e-transport"
  }}]')"
volume_state_file="$archive_tmp/volume-created"
volume_inspect_error=0
volume_consumers=""
docker() {
  if [[ "$1 $2" == "volume inspect" ]]; then
    [[ "$volume_inspect_error" == "0" && -f "$volume_state_file" ]] || return 1
    printf '%s' "$volume_fixture"
  elif [[ "$1 $2" == "volume ls" ]]; then
    if [[ -f "$volume_state_file" ]]; then printf '%s\n' "$volume_name"; fi
    return 0
  elif [[ "$1 $2" == "volume create" ]]; then
    touch "$volume_state_file"
    printf '%s\n' "$volume_name"
  elif [[ "$1 $2" == "ps -aq" ]]; then
    printf '%s' "$volume_consumers"
  else
    return 2
  fi
}

runtime_preview_ensure_owned_volume \
  "$volume_name" runtime_e2e_transport runtime-e2e-transport
[[ -f "$volume_state_file" ]]
volume_consumers=unexpected-container
if runtime_preview_verify_owned_volume \
  "$volume_name" runtime_e2e_transport runtime-e2e-transport yes >/dev/null 2>&1; then
  echo "pre-existing volume consumer was accepted" >&2
  exit 1
fi
volume_consumers=""
exact_volume_fixture="$volume_fixture"
volume_fixture="$(jq -c '.[0].Labels["com.autonomia.purpose"]="other-project"' \
  <<<"$exact_volume_fixture")"
if runtime_preview_verify_owned_volume \
  "$volume_name" runtime_e2e_transport runtime-e2e-transport yes >/dev/null 2>&1; then
  echo "foreign volume labels were accepted" >&2
  exit 1
fi
volume_fixture="$exact_volume_fixture"
volume_inspect_error=1
if runtime_preview_volume_inspect "$volume_name" >/dev/null 2>&1; then
  echo "Docker inspect failure was confused with an absent volume" >&2
  exit 1
fi
unset -f docker

echo "EasyPanel runtime-preview API parser: passed"
