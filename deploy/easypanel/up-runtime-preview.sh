#!/usr/bin/env bash
set -euo pipefail
case $- in *x*) set +x ;; esac

[[ $# -eq 3 ]] || { echo "Usage: $0 RUN_ROOT ENV_FILE APPSELL_SOURCE_SHA" >&2; exit 2; }
[[ "${CONFIRM_RUNTIME_PREVIEW_UP:-}" == "appsell-runtime-preview-77" ]] || {
  echo "set CONFIRM_RUNTIME_PREVIEW_UP=appsell-runtime-preview-77" >&2
  exit 2
}
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-preview-common.sh
source "$here/runtime-preview-common.sh"
runtime_preview_require_root
runtime_preview_require_commands curl docker jq realpath stat
runtime_preview_assert_run_root "$1" "$3"
runtime_preview_assert_env_file "$2"
source_sha="$3"
"$here/preflight-runtime-preview.sh" "$RUNTIME_PREVIEW_RUN_ROOT" "$RUNTIME_PREVIEW_ENV_FILE" "$source_sha"
runtime_preview_api_login

record_state="$(runtime_preview_service_state)"
token_fingerprint=""
source_configured=0
if [[ "$record_state" == "absent" ]]; then
  runtime_preview_write_attestation intent
else
  runtime_preview_verify_attestation 'stopped|provisioning|configured'
  token_fingerprint="$(runtime_preview_attestation_value token_sha256)"
fi
rollback_runtime_preview() {
  original_status=$?
  trap - ERR INT TERM EXIT
  (( original_status != 0 )) || original_status=1
  if [[ -z "$token_fingerprint" ]]; then
    echo "runtime-preview creation outcome is unowned; intent preserved for manual recovery" >&2
    exit "$original_status"
  fi
  ownership_mode=no
  [[ "$source_configured" == "1" ]] && ownership_mode=yes
  if [[ "$(runtime_preview_service_state 2>/dev/null || true)" != "present" ]] || \
     ! runtime_preview_verify_live_record "$ownership_mode" >/dev/null 2>&1; then
    runtime_preview_write_attestation rollback_failed "$token_fingerprint" >/dev/null 2>&1 || true
    echo "runtime-preview rollback ownership changed; no resource was removed" >&2
    exit "$original_status"
  fi
  if runtime_preview_compose_down >/dev/null 2>&1 && runtime_preview_remove_easypanel_env >/dev/null 2>&1; then
    if [[ "$source_configured" == "1" ]]; then
      runtime_preview_write_attestation stopped "$token_fingerprint" >/dev/null 2>&1 || true
    else
      runtime_preview_write_attestation provisioning "$token_fingerprint" >/dev/null 2>&1 || true
    fi
  else
    runtime_preview_write_attestation rollback_failed "$token_fingerprint" >/dev/null 2>&1 || true
    echo "runtime-preview rollback failed closed; inspect exact Issue #77 containers before retry" >&2
  fi
  exit "$original_status"
}
trap rollback_runtime_preview ERR INT TERM EXIT

if [[ "$record_state" == "absent" ]]; then
  payload="$(jq -cn --arg projectName "$RUNTIME_PREVIEW_PROJECT" --arg serviceName "$RUNTIME_PREVIEW_SERVICE" \
    '{json:{projectName:$projectName,serviceName:$serviceName,env:"",createDotEnv:false,domains:[]}}')"
  response="$(runtime_preview_api_post /api/rpc/services/compose/createService "$payload")"
  runtime_preview_expect_api_success "$response" createService
  token_fingerprint="$(runtime_preview_created_record_token_fingerprint "$response")"
  runtime_preview_write_attestation provisioning "$token_fingerprint"
else
  runtime_preview_write_attestation provisioning "$token_fingerprint"
fi

compose_file="$RUNTIME_PREVIEW_RUN_ROOT/config/easypanel/compose.runtime-preview.yaml"
runtime_preview_assert_protected_file "$compose_file"
payload="$(jq -cn --arg projectName "$RUNTIME_PREVIEW_PROJECT" --arg serviceName "$RUNTIME_PREVIEW_SERVICE" \
  --rawfile content "$compose_file" \
  '{json:{projectName:$projectName,serviceName:$serviceName,content:$content}}')"
response="$(runtime_preview_api_post /api/rpc/services/compose/updateSourceInline "$payload")"
runtime_preview_expect_api_success "$response" updateSourceInline

easypanel_env="/etc/easypanel/projects/$RUNTIME_PREVIEW_PROJECT/$RUNTIME_PREVIEW_SERVICE/code/.env"
[[ -d "$(dirname "$easypanel_env")" && ! -L "$(dirname "$easypanel_env")" ]] || { echo "EasyPanel code directory is missing" >&2; exit 1; }
[[ "$(realpath -e "$(dirname "$easypanel_env")")" == "/etc/easypanel/projects/sell-autonomia/runtime-preview/code" ]] || exit 1
runtime_preview_assert_protected_directory "$(dirname "$easypanel_env")"
runtime_preview_atomic_install_env "$RUNTIME_PREVIEW_ENV_FILE" "$easypanel_env"
[[ "$(stat -c '%a:%u:%g:%h' "$easypanel_env")" == "600:0:0:1" ]] || exit 1
[[ "$(sha256sum "$easypanel_env" | awk '{print $1}')" == "$(sha256sum "$RUNTIME_PREVIEW_ENV_FILE" | awk '{print $1}')" ]] || exit 1
payload="$(jq -cn --arg projectName "$RUNTIME_PREVIEW_PROJECT" --arg serviceName "$RUNTIME_PREVIEW_SERVICE" \
  '{json:{projectName:$projectName,serviceName:$serviceName,env:"",createDotEnv:false}}')"
response="$(runtime_preview_api_post /api/rpc/services/compose/updateEnv "$payload")"
runtime_preview_expect_api_success "$response" updateEnv
[[ "$(stat -c '%a:%u:%g:%h' "$easypanel_env")" == "600:0:0:1" ]] || exit 1
[[ "$(sha256sum "$easypanel_env" | awk '{print $1}')" == "$(sha256sum "$RUNTIME_PREVIEW_ENV_FILE" | awk '{print $1}')" ]] || exit 1
runtime_preview_verify_live_record yes
source_configured=1
runtime_preview_write_attestation configured "$token_fingerprint"

payload="$(jq -cn --arg projectName "$RUNTIME_PREVIEW_PROJECT" --arg serviceName "$RUNTIME_PREVIEW_SERVICE" \
  '{json:{projectName:$projectName,serviceName:$serviceName,forceRebuild:false}}')"
response="$(runtime_preview_api_post /api/rpc/services/compose/deployService "$payload")"
runtime_preview_expect_api_success "$response" deployService
"$here/verify-runtime-preview.sh" "$RUNTIME_PREVIEW_RUN_ROOT" "$RUNTIME_PREVIEW_ENV_FILE" "$source_sha"
runtime_preview_write_attestation active "$token_fingerprint"
trap - ERR INT TERM EXIT
echo "runtime-preview created and deployed only in sell-autonomia/runtime-preview"
