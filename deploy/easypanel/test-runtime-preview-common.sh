#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-preview-common.sh
source "$here/runtime-preview-common.sh"
RUNTIME_PREVIEW_API_TOKEN=fixture-token-long-enough-for-tests
mock_mode=absent

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

echo "EasyPanel runtime-preview API parser: passed"
