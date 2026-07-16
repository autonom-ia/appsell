#!/usr/bin/env bash
set -euo pipefail
case $- in *x*) set +x ;; esac

[[ $# -eq 3 ]] || { echo "Usage: $0 RUN_ROOT ENV_FILE APPSELL_SOURCE_SHA" >&2; exit 2; }
[[ "${CONFIRM_RUNTIME_PREVIEW_PRIVATE:-}" == "sell-autonomia-private-77" ]] || {
  echo "set CONFIRM_RUNTIME_PREVIEW_PRIVATE=sell-autonomia-private-77" >&2
  exit 2
}
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-preview-common.sh
source "$here/runtime-preview-common.sh"
runtime_preview_require_root
runtime_preview_require_commands curl docker flock jq realpath stat
runtime_preview_assert_run_root "$1" "$3"
runtime_preview_assert_env_file "$2"
source_sha="$3"
runtime_preview_acquire_transition_lock "$RUNTIME_PREVIEW_TRANSITION_LOCK"
runtime_preview_api_login
runtime_preview_verify_attestation active
runtime_preview_verify_live_record yes
runtime_preview_verify_service_snapshot "$RUNTIME_PREVIEW_RUN_ROOT/protected-services.before.tsv"
public_attestation="$RUNTIME_PREVIEW_RUN_ROOT/public-domain.attestation.json"
activation_intent="$RUNTIME_PREVIEW_RUN_ROOT/public-domain.intent.json"
[[ ! -e "$activation_intent" && ! -L "$activation_intent" ]] || {
  echo "refusing deactivation while a public-domain activation intent exists" >&2
  exit 1
}
deactivation_intent="$RUNTIME_PREVIEW_RUN_ROOT/public-domain.deactivation-intent.json"

if domain_json="$(runtime_preview_exact_public_domain_json)"; then
  domain_status=0
  current_domain_id="$(jq -er '.id' <<<"$domain_json")"
else
  domain_status=$?
  [[ "$domain_status" == "3" ]] || exit "$domain_status"
  current_domain_id=""
  runtime_preview_verify_public_domain_absent
fi

if [[ -e "$deactivation_intent" || -L "$deactivation_intent" ]]; then
  runtime_preview_assert_protected_file "$deactivation_intent"
  expected_domain_id="$(jq -er \
    --arg project "$RUNTIME_PREVIEW_PROJECT" --arg service "$RUNTIME_PREVIEW_SERVICE" \
    --arg source_sha "$source_sha" --arg host "$RUNTIME_PREVIEW_PUBLIC_HOST" '
      select(
        .schema_version == "autonomia.easypanel-public-domain-deactivation.v1" and
        .project == $project and .service == $service and .source_sha == $source_sha and
        .host == $host and
        (.domain_id | type == "string" and test("^[A-Za-z0-9._-]{8,128}$")) and
        (.created_at | type == "string" and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T"))
      ) | .domain_id
    ' "$deactivation_intent")"
else
  [[ "$domain_status" == "0" ]] || {
    echo "public domain is already absent and no deactivation intent can prove ownership" >&2
    exit 1
  }
  expected_domain_id="$current_domain_id"
  temporary="$(mktemp "$RUNTIME_PREVIEW_RUN_ROOT/.public-domain-deactivation.XXXXXX")"
  chmod 0600 "$temporary"
  jq -n \
    --arg schema_version autonomia.easypanel-public-domain-deactivation.v1 \
    --arg project "$RUNTIME_PREVIEW_PROJECT" --arg service "$RUNTIME_PREVIEW_SERVICE" \
    --arg source_sha "$source_sha" --arg host "$RUNTIME_PREVIEW_PUBLIC_HOST" \
    --arg domain_id "$expected_domain_id" --arg created_at "$(date -u +%FT%TZ)" \
    '{schema_version:$schema_version,project:$project,service:$service,source_sha:$source_sha,
      host:$host,domain_id:$domain_id,created_at:$created_at}' >"$temporary"
  chown root:root "$temporary"
  mv -f -- "$temporary" "$deactivation_intent"
  chmod 0600 "$deactivation_intent"
fi

if [[ "$domain_status" == "0" ]]; then
  [[ "$current_domain_id" == "$expected_domain_id" ]] || {
    echo "deactivation intent does not own the current public domain" >&2
    exit 1
  }
  runtime_preview_delete_exact_public_domain
else
  runtime_preview_verify_public_domain_absent
fi
if [[ -e "$public_attestation" || -L "$public_attestation" ]]; then
  runtime_preview_assert_protected_file "$public_attestation"
  rm -f -- "$public_attestation"
fi
runtime_preview_assert_protected_file "$deactivation_intent"
rm -f -- "$deactivation_intent"
runtime_preview_verify_live_record yes
runtime_preview_verify_service_snapshot "$RUNTIME_PREVIEW_RUN_ROOT/protected-services.before.tsv"
echo "runtime-preview returned to private loopback-only access"
