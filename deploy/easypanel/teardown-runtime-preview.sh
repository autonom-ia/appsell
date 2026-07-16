#!/usr/bin/env bash
set -euo pipefail
case $- in *x*) set +x ;; esac

[[ $# -eq 3 ]] || { echo "Usage: $0 RUN_ROOT ENV_FILE APPSELL_SOURCE_SHA" >&2; exit 2; }
[[ "${CONFIRM_RUNTIME_PREVIEW_TEARDOWN:-}" == "appsell-runtime-preview-77-teardown" ]] || {
  echo "set CONFIRM_RUNTIME_PREVIEW_TEARDOWN=appsell-runtime-preview-77-teardown" >&2
  exit 2
}
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-preview-common.sh
source "$here/runtime-preview-common.sh"
runtime_preview_require_root
runtime_preview_require_commands curl docker flock grep jq realpath sed stat
runtime_preview_assert_run_root "$1" "$3"
runtime_preview_assert_env_file "$2"
source_sha="$3"
runtime_preview_acquire_transition_lock "$RUNTIME_PREVIEW_TRANSITION_LOCK"
runtime_preview_verify_attestation 'active|configured|stopped'
token_fingerprint="$(runtime_preview_attestation_value token_sha256)"

mapfile -t ids < <(docker ps -aq --filter "label=com.docker.compose.project=$RUNTIME_PREVIEW_COMPOSE_PROJECT")
for id in "${ids[@]}"; do
  [[ "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$id")" == "$RUNTIME_PREVIEW_COMPOSE_PROJECT" ]] || exit 2
  [[ "$(docker inspect --format '{{index .Config.Labels "com.autonomia.issue"}}' "$id")" == "77" ]] || exit 2
  [[ "$(docker inspect --format '{{index .Config.Labels "com.autonomia.source-sha"}}' "$id")" == "$source_sha" ]] || exit 2
done

runtime_preview_api_login
[[ "$(runtime_preview_service_state)" == "present" ]] || { echo "attested EasyPanel runtime-preview record is absent" >&2; exit 2; }
runtime_preview_verify_live_record yes
runtime_preview_verify_public_domain_absent || {
  echo "deactivate the exact public domain before stopping runtime-preview" >&2
  exit 2
}
runtime_preview_compose_down
runtime_preview_remove_easypanel_env
runtime_preview_write_attestation stopped "$token_fingerprint"
runtime_preview_verify_service_snapshot "$RUNTIME_PREVIEW_RUN_ROOT/protected-services.before.tsv"
"$RUNTIME_PREVIEW_RUN_ROOT/config/easypanel/verify-sell-isolation.sh" >/dev/null
echo "stopped only sell-autonomia/runtime-preview; EasyPanel record, database, volume and native services preserved"
