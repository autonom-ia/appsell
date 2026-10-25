#!/usr/bin/env bash
set -euo pipefail
case $- in *x*) set +x ;; esac

[[ $# -eq 3 ]] || { echo "Usage: $0 RUN_ROOT ENV_FILE APPSELL_SOURCE_SHA" >&2; exit 2; }
[[ "${CONFIRM_RUNTIME_PREVIEW_PUBLIC:-}" == "sell-autonomia-public-77" ]] || {
  echo "set CONFIRM_RUNTIME_PREVIEW_PUBLIC=sell-autonomia-public-77" >&2
  exit 2
}
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-preview-common.sh
source "$here/runtime-preview-common.sh"
runtime_preview_require_root
runtime_preview_require_commands curl dig docker flock jq openssl realpath sha256sum stat timeout
runtime_preview_assert_run_root "$1" "$3"
runtime_preview_assert_env_file "$2"
source_sha="$3"
runtime_preview_acquire_transition_lock "$RUNTIME_PREVIEW_TRANSITION_LOCK"
[[ "$(runtime_preview_env_value APPSELL_PUBLIC_ORIGIN)" == "$RUNTIME_PREVIEW_PUBLIC_ORIGIN" ]] || {
  echo "runtime-preview env is not pinned to the public HTTPS origin" >&2
  exit 1
}

public_ipv4="$(runtime_preview_public_ipv4)"
runtime_preview_verify_public_dns

runtime_preview_api_login
runtime_preview_verify_attestation active
runtime_preview_verify_live_record yes
runtime_preview_verify_service_snapshot "$RUNTIME_PREVIEW_RUN_ROOT/protected-services.before.tsv"

activation_complete=0
activation_owned=0
create_result_uncertain=0
preserve_intent_on_exit=0
headers=""
body=""
certificate=""
intent="$RUNTIME_PREVIEW_RUN_ROOT/public-domain.intent.json"
public_attestation="$RUNTIME_PREVIEW_RUN_ROOT/public-domain.attestation.json"

remove_owned_intent() {
  [[ -e "$intent" || -L "$intent" ]] || return 0
  runtime_preview_assert_protected_file "$intent" || return 1
  jq -e \
    --arg project "$RUNTIME_PREVIEW_PROJECT" --arg service "$RUNTIME_PREVIEW_SERVICE" \
    --arg source_sha "$source_sha" --arg host "$RUNTIME_PREVIEW_PUBLIC_HOST" '
      .schema_version == "autonomia.easypanel-public-domain-intent.v1" and
      .project == $project and .service == $service and .source_sha == $source_sha and
      .host == $host and .previous_primary_domain_id == null
    ' "$intent" >/dev/null || return 1
  rm -f -- "$intent"
}

rollback_activation() {
  original_status=$?
  trap - EXIT INT TERM
  [[ -z "$headers" ]] || rm -f -- "$headers"
  [[ -z "$body" ]] || rm -f -- "$body"
  [[ -z "$certificate" ]] || rm -f -- "$certificate"
  rollback_status=0
  if [[ "$activation_complete" != "1" && "$activation_owned" == "1" ]]; then
    keep_intent="$preserve_intent_on_exit"
    if [[ "$create_result_uncertain" == "1" ]]; then
      uncertain_domain_state=absent
      for _rollback_reconcile_attempt in $(seq 1 15); do
        if runtime_preview_exact_public_domain_json >/dev/null 2>&1; then
          uncertain_domain_state=present
          break
        else
          domain_status=$?
        fi
        if [[ "$domain_status" != "3" ]]; then
          uncertain_domain_state=unknown
          rollback_status=1
          break
        fi
        sleep 2
      done
      [[ "$uncertain_domain_state" == "present" ]] || keep_intent=1
    fi
    if runtime_preview_exact_public_domain_json >/dev/null 2>&1; then
      runtime_preview_delete_exact_public_domain || rollback_status=1
    else
      domain_status=$?
      [[ "$domain_status" == "3" ]] || rollback_status=1
      if [[ "$rollback_status" == "0" ]]; then
        runtime_preview_verify_public_domain_absent || rollback_status=1
      fi
    fi
    if [[ -e "$public_attestation" || -L "$public_attestation" ]]; then
      runtime_preview_assert_protected_file "$public_attestation" && rm -f -- "$public_attestation" || rollback_status=1
    fi
    runtime_preview_verify_service_snapshot "$RUNTIME_PREVIEW_RUN_ROOT/protected-services.before.tsv" || rollback_status=1
    if [[ "$rollback_status" != "0" ]]; then
      keep_intent=1
    fi
    if [[ "$keep_intent" == "0" ]]; then
      remove_owned_intent || rollback_status=1
    else
      echo "public activation result is uncertain; ownership intent was preserved for reconciliation" >&2
    fi
  fi
  if [[ "$rollback_status" != "0" ]]; then
    echo "public activation failed and ownership-safe domain rollback did not complete" >&2
    exit 1
  fi
  exit "$original_status"
}
trap rollback_activation EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

set +e
domain_json="$(runtime_preview_exact_public_domain_json)"
domain_status=$?
set -e
if [[ "$domain_status" == "3" ]]; then
  global_domains="$(runtime_preview_list_domains all)"
  jq -e '.json | type == "array"' <<<"$global_domains" >/dev/null || {
    echo "EasyPanel global domain list has an invalid type" >&2
    exit 1
  }
  [[ "$(jq -r --arg host "$RUNTIME_PREVIEW_PUBLIC_HOST" '[.json[] | select(.host == $host)] | length' \
    <<<"$global_domains")" == "0" ]] || {
    echo "public host already belongs to another EasyPanel domain" >&2
    exit 1
  }
  create_domain=1
  if [[ -e "$intent" || -L "$intent" ]]; then
    runtime_preview_assert_protected_file "$intent"
    jq -e \
      --arg project "$RUNTIME_PREVIEW_PROJECT" --arg service "$RUNTIME_PREVIEW_SERVICE" \
      --arg source_sha "$source_sha" --arg host "$RUNTIME_PREVIEW_PUBLIC_HOST" '
        .schema_version == "autonomia.easypanel-public-domain-intent.v1" and
        .project == $project and .service == $service and .source_sha == $source_sha and
        .host == $host and .previous_primary_domain_id == null
      ' "$intent" >/dev/null
    activation_owned=1
    preserve_intent_on_exit=1
    for _resume_reconcile_attempt in $(seq 1 15); do
      if domain_json="$(runtime_preview_exact_public_domain_json 2>/dev/null)"; then
        domain_status=0
        create_domain=0
        preserve_intent_on_exit=0
        break
      else
        resume_status=$?
      fi
      [[ "$resume_status" == "3" ]] || exit "$resume_status"
      sleep 2
    done
    if [[ "$create_domain" == "1" && \
      "${CONFIRM_RUNTIME_PREVIEW_PUBLIC_RETRY:-}" != "sell-autonomia-public-retry-77" ]]; then
      echo "activation intent is unresolved; set CONFIRM_RUNTIME_PREVIEW_PUBLIC_RETRY=sell-autonomia-public-retry-77 only after reviewing the retained intent" >&2
      exit 1
    fi
    if [[ "$create_domain" == "1" ]]; then
      preserve_intent_on_exit=0
    fi
  else
    intent_temporary="$(mktemp "$RUNTIME_PREVIEW_RUN_ROOT/.public-domain-intent.XXXXXX")"
    chmod 0600 "$intent_temporary"
    jq -n \
      --arg schema_version autonomia.easypanel-public-domain-intent.v1 \
      --arg project "$RUNTIME_PREVIEW_PROJECT" --arg service "$RUNTIME_PREVIEW_SERVICE" \
      --arg source_sha "$source_sha" --arg host "$RUNTIME_PREVIEW_PUBLIC_HOST" \
      --arg created_at "$(date -u +%FT%TZ)" \
      '{schema_version:$schema_version,project:$project,service:$service,source_sha:$source_sha,
        host:$host,previous_primary_domain_id:null,created_at:$created_at}' >"$intent_temporary"
    chown root:root "$intent_temporary"
    mv -f -- "$intent_temporary" "$intent"
    chmod 0600 "$intent"
    activation_owned=1
  fi
  if [[ "$create_domain" == "1" ]]; then
    payload="$(jq -cn \
      --arg host "$RUNTIME_PREVIEW_PUBLIC_HOST" \
      --arg projectName "$RUNTIME_PREVIEW_PROJECT" \
      --arg serviceName "$RUNTIME_PREVIEW_SERVICE" \
      '{json:{id:"",certificateResolver:"",host:$host,https:true,middlewares:[],path:"/",wildcard:false,
        destinationType:"service",serviceDestination:{composeService:"sell-web",path:"/",port:13200,
        projectName:$projectName,protocol:"http",serviceName:$serviceName}}}')"
    create_result_uncertain=1
    response="$(runtime_preview_domain_api_post /api/rpc/domains/createDomain "$payload")"
    runtime_preview_expect_api_success "$response" createDomain
    create_result_uncertain=0
  fi
elif [[ "$domain_status" != "0" ]]; then
  exit "$domain_status"
elif [[ -e "$intent" || -L "$intent" ]]; then
  runtime_preview_assert_protected_file "$intent"
  jq -e \
    --arg project "$RUNTIME_PREVIEW_PROJECT" --arg service "$RUNTIME_PREVIEW_SERVICE" \
    --arg source_sha "$source_sha" --arg host "$RUNTIME_PREVIEW_PUBLIC_HOST" '
      .schema_version == "autonomia.easypanel-public-domain-intent.v1" and
      .project == $project and .service == $service and .source_sha == $source_sha and
      .host == $host and .previous_primary_domain_id == null
    ' "$intent" >/dev/null
  activation_owned=1
fi

domain_ready=0
for _domain_attempt in $(seq 1 30); do
  if domain_json="$(runtime_preview_exact_public_domain_json 2>/dev/null)"; then
    domain_id="$(jq -er '.id' <<<"$domain_json")"
    inspect_payload="$(jq -cn --arg projectName "$RUNTIME_PREVIEW_PROJECT" --arg serviceName "$RUNTIME_PREVIEW_SERVICE" \
      '{json:{projectName:$projectName,serviceName:$serviceName}}')"
    inspect_response="$(runtime_preview_api_inspect "$inspect_payload")"
    primary_domain_id="$(jq -r '.json.primaryDomainId // empty' <<<"$inspect_response")"
    if [[ "$primary_domain_id" != "$domain_id" ]]; then
      set_primary_payload="$(jq -cn --arg id "$domain_id" '{json:{id:$id}}')"
      set_primary_response="$(runtime_preview_domain_api_post /api/rpc/domains/setPrimaryDomain "$set_primary_payload")"
      runtime_preview_expect_api_success "$set_primary_response" setPrimaryDomain
    fi
    if [[ "$(runtime_preview_verify_public_domain)" == "$domain_id" ]]; then
      domain_ready=1
      break
    fi
  fi
  sleep 2
done
[[ "$domain_ready" == "1" ]] || { echo "EasyPanel public domain did not converge" >&2; exit 1; }

public_ready=0
headers="$(mktemp)"
body="$(mktemp)"
certificate="$(mktemp)"
for _public_attempt in $(seq 1 60); do
  if curl -fsS --connect-timeout 5 --max-time 20 -D "$headers" -o "$body" \
    "$RUNTIME_PREVIEW_PUBLIC_ORIGIN/" && \
    grep -Eiq '^x-frame-options:[[:space:]]*DENY[[:space:]]*$' "$headers" && \
    grep -Eiq '^x-content-type-options:[[:space:]]*nosniff[[:space:]]*$' "$headers" && \
    grep -Eiq '^strict-transport-security:[[:space:]]*max-age=31536000;[[:space:]]*includeSubDomains[[:space:]]*$' "$headers" && \
    grep -Fq 'Autonom.ia' "$body"; then
    public_ready=1
    break
  fi
  sleep 5
done
[[ "$public_ready" == "1" ]] || { echo "public HTTPS endpoint did not become ready" >&2; exit 1; }

http_result="$(curl -sS -o /dev/null --connect-timeout 5 --max-time 15 \
  -w '%{http_code}|%{redirect_url}' "http://$RUNTIME_PREVIEW_PUBLIC_HOST/")"
case "$http_result" in
  "301|$RUNTIME_PREVIEW_PUBLIC_ORIGIN/"|"308|$RUNTIME_PREVIEW_PUBLIC_ORIGIN/") ;;
  *) echo "public HTTP endpoint did not redirect exactly to HTTPS" >&2; exit 1 ;;
esac

timeout 15 openssl s_client -connect "$RUNTIME_PREVIEW_PUBLIC_HOST:443" \
  -servername "$RUNTIME_PREVIEW_PUBLIC_HOST" </dev/null 2>/dev/null \
  | openssl x509 -out "$certificate"
openssl x509 -in "$certificate" -noout -checkhost "$RUNTIME_PREVIEW_PUBLIC_HOST" >/dev/null
openssl x509 -in "$certificate" -noout -checkend 604800 >/dev/null
certificate_sha256="$(openssl x509 -in "$certificate" -outform DER | sha256sum | awk '{print $1}')"

temporary="$(mktemp "$RUNTIME_PREVIEW_RUN_ROOT/.public-domain.XXXXXX")"
chmod 0600 "$temporary"
jq -n \
  --arg schema_version autonomia.easypanel-public-domain.v1 \
  --arg project "$RUNTIME_PREVIEW_PROJECT" --arg service "$RUNTIME_PREVIEW_SERVICE" \
  --arg source_sha "$source_sha" --arg domain_id "$domain_id" \
  --arg host "$RUNTIME_PREVIEW_PUBLIC_HOST" --arg origin "$RUNTIME_PREVIEW_PUBLIC_ORIGIN" \
  --arg ipv4 "$public_ipv4" --arg certificate_sha256 "$certificate_sha256" \
  --arg checked_at "$(date -u +%FT%TZ)" \
  '{schema_version:$schema_version,project:$project,service:$service,source_sha:$source_sha,
    domain_id:$domain_id,host:$host,origin:$origin,ipv4:$ipv4,
    certificate_sha256:$certificate_sha256,checked_at:$checked_at}' >"$temporary"
chown root:root "$temporary"
mv -f -- "$temporary" "$public_attestation"
chmod 0600 "$public_attestation"
[[ "$(stat -c '%a:%u:%g:%h' "$public_attestation")" == "600:0:0:1" ]]
runtime_preview_verify_public_attestation
runtime_preview_verify_live_record yes
runtime_preview_verify_service_snapshot "$RUNTIME_PREVIEW_RUN_ROOT/protected-services.before.tsv"
remove_owned_intent
activation_complete=1
echo "public runtime-preview active at $RUNTIME_PREVIEW_PUBLIC_ORIGIN with valid TLS"
