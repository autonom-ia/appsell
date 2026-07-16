#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-preview-common.sh
source "$here/runtime-preview-common.sh"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
deleted_marker="$tmp/deleted"
MOCK_INVALID_LIST=0
DELETE_MODE=success

domain_id=domain-runtime-preview-77
exact_domain="$(jq -cn \
  --arg id "$domain_id" --arg host "$RUNTIME_PREVIEW_PUBLIC_HOST" \
  --arg project "$RUNTIME_PREVIEW_PROJECT" --arg service "$RUNTIME_PREVIEW_SERVICE" '
  {id:$id,certificateResolver:"",host:$host,https:true,middlewares:[],path:"/",wildcard:false,
   destinationType:"service",serviceDestination:{composeService:"sell-web",path:"/",port:13200,
   projectName:$project,protocol:"http",serviceName:$service}}')"
MOCK_SERVICE_RESPONSE="$(jq -cn --argjson domain "$exact_domain" '{json:[$domain]}')"
MOCK_GLOBAL_RESPONSE="$MOCK_SERVICE_RESPONSE"
MOCK_INSPECT_RESPONSE="$(jq -cn --arg id "$domain_id" '{json:{primaryDomainId:$id}}')"

runtime_preview_list_domains() {
  if [[ "$MOCK_INVALID_LIST" == "1" ]]; then printf '%s' '{"json":{}}'; return; fi
  if [[ -f "$deleted_marker" ]]; then printf '%s' '{"json":[]}'; return; fi
  if [[ "${1:-service}" == "all" ]]; then
    printf '%s' "$MOCK_GLOBAL_RESPONSE"
  else
    printf '%s' "$MOCK_SERVICE_RESPONSE"
  fi
}
runtime_preview_api_inspect() {
  if [[ -f "$deleted_marker" ]]; then printf '%s' '{"json":{"primaryDomainId":null}}'; else printf '%s' "$MOCK_INSPECT_RESPONSE"; fi
}
runtime_preview_domain_api_post() {
  [[ "$1" == "/api/rpc/domains/deleteDomain" ]]
  [[ "$(jq -er '.json.id' <<<"$2")" == "$domain_id" ]]
  touch "$deleted_marker"
  [[ "$DELETE_MODE" != "response-lost" ]] || return 1
  printf '%s' '{"json":true}'
}

[[ "$(runtime_preview_exact_public_domain_json)" == "$exact_domain" ]]
[[ "$(runtime_preview_verify_public_domain)" == "$domain_id" ]]

MOCK_SERVICE_RESPONSE='{"json":[]}'
set +e
runtime_preview_exact_public_domain_json >/dev/null 2>&1
status=$?
set -e
[[ "$status" == "3" ]] || { echo "absent public domain did not return the resumable status" >&2; exit 1; }

for mutation in \
  '.host="other.autonomia.site"' \
  '.https=false' \
  '.path="/wrong"' \
  '.wildcard=true' \
  '.middlewares=["unexpected"]' \
  '.serviceDestination.composeService="domain-api"' \
  '.serviceDestination.port=15800' \
  '.serviceDestination.projectName="flow"'; do
  mutated="$(jq -c "$mutation" <<<"$exact_domain")"
  MOCK_SERVICE_RESPONSE="$(jq -cn --argjson domain "$mutated" '{json:[$domain]}')"
  if runtime_preview_exact_public_domain_json >/dev/null 2>&1; then
    echo "invalid public domain mutation was accepted: $mutation" >&2
    exit 1
  fi
done

MOCK_SERVICE_RESPONSE="$(jq -cn --argjson domain "$exact_domain" '{json:[$domain,$domain]}')"
if runtime_preview_exact_public_domain_json >/dev/null 2>&1; then
  echo "duplicate service domains were accepted" >&2
  exit 1
fi

MOCK_SERVICE_RESPONSE="$(jq -cn --argjson domain "$exact_domain" '{json:[$domain]}')"
MOCK_GLOBAL_RESPONSE="$(jq -cn --argjson domain "$exact_domain" '{json:[$domain,$domain]}')"
if runtime_preview_verify_public_domain >/dev/null 2>&1; then
  echo "duplicate global public hosts were accepted" >&2
  exit 1
fi

MOCK_GLOBAL_RESPONSE='{"json":{}}'
if runtime_preview_verify_public_domain >/dev/null 2>&1; then
  echo "invalid global domain-list type was accepted" >&2
  exit 1
fi

foreign_global_domain="$(jq -c '.id="foreign-domain-77"' <<<"$exact_domain")"
MOCK_GLOBAL_RESPONSE="$(jq -cn --argjson domain "$foreign_global_domain" '{json:[$domain]}')"
if runtime_preview_verify_public_domain >/dev/null 2>&1; then
  echo "foreign global domain id was accepted for the public host" >&2
  exit 1
fi

MOCK_GLOBAL_RESPONSE="$MOCK_SERVICE_RESPONSE"
MOCK_INSPECT_RESPONSE='{"json":{"primaryDomainId":"other-domain"}}'
if runtime_preview_verify_public_domain >/dev/null 2>&1; then
  echo "mismatched primary domain was accepted" >&2
  exit 1
fi

MOCK_SERVICE_RESPONSE="$(jq -cn --argjson domain "$exact_domain" '{json:[$domain]}')"
MOCK_GLOBAL_RESPONSE="$MOCK_SERVICE_RESPONSE"
MOCK_INSPECT_RESPONSE="$(jq -cn --arg id "$domain_id" '{json:{primaryDomainId:$id}}')"
runtime_preview_delete_exact_public_domain
[[ -f "$deleted_marker" ]]
runtime_preview_verify_public_domain_absent
MOCK_INVALID_LIST=1
if runtime_preview_verify_public_domain_absent >/dev/null 2>&1; then
  echo "invalid EasyPanel domain-list type was accepted as absence" >&2
  exit 1
fi
MOCK_INVALID_LIST=0
rm -f -- "$deleted_marker"
DELETE_MODE=response-lost
runtime_preview_delete_exact_public_domain
[[ -f "$deleted_marker" ]]
runtime_preview_verify_public_domain_absent

dns_mode=valid
dig() {
  case "$2:$dns_mode" in
    A:valid|A:unexpected-aaaa) printf '%s\n' "$RUNTIME_PREVIEW_PUBLIC_IPV4" ;;
    AAAA:unexpected-aaaa) printf '%s\n' '2a02:4780:14:72a4::1' ;;
    AAAA:valid|CNAME:valid|CNAME:unexpected-aaaa) ;;
    *) return 2 ;;
  esac
}
runtime_preview_verify_public_dns
dns_mode=unexpected-aaaa
if runtime_preview_verify_public_dns >/dev/null 2>&1; then
  echo "unexpected public AAAA record was accepted" >&2
  exit 1
fi
unset -f dig

echo "EasyPanel runtime-preview public domain contract: passed"
