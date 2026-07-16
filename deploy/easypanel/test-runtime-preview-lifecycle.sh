#!/usr/bin/env bash
set -euo pipefail

[[ "$(id -u)" == "0" ]] || { echo "lifecycle fixture must run as root" >&2; exit 2; }
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-preview-common.sh
source "$here/runtime-preview-common.sh"
source_sha=ffffffffffffffffffffffffffffffffffffffff
run_root="/opt/autonomia-sell-preview/issue-77/${source_sha:0:12}"
env_root="$(mktemp -d)"
lock_holder_pid=""
[[ ! -e "$run_root" && ! -L "$run_root" ]] || { echo "lifecycle fixture path collision" >&2; exit 2; }
cleanup() {
  if [[ -n "$lock_holder_pid" ]]; then
    kill "$lock_holder_pid" >/dev/null 2>&1 || true
    wait "$lock_holder_pid" >/dev/null 2>&1 || true
  fi
  rm -rf --one-file-system -- "$run_root" "$env_root"
}
trap cleanup EXIT
install -d -o root -g root -m 0700 "$run_root/config/easypanel"
printf 'services: {}\n' >"$run_root/config/easypanel/compose.runtime-preview.yaml"
chown root:root "$run_root/config/easypanel/compose.runtime-preview.yaml"
chmod 0600 "$run_root/config/easypanel/compose.runtime-preview.yaml"
env_file="$env_root/runtime-preview.env"
printf 'APPSELL_SOURCE_SHA=%s\n' "$source_sha" >"$env_file"
chown root:root "$env_file"
chmod 0600 "$env_file"

fixture_lock="$env_root/transition.lock"
fixture_marker="$env_root/transition-held"
install -o root -g root -m 0600 /dev/null "$fixture_lock"
bash -c 'exec 9<>"$1"; flock -n 9; touch "$2"; exec sleep 30' \
  bash "$fixture_lock" "$fixture_marker" &
lock_holder_pid=$!
for _lock_wait in $(seq 1 100); do
  [[ -f "$fixture_marker" ]] && break
  sleep 0.02
done
[[ -f "$fixture_marker" ]] || { echo "lock fixture did not acquire contention lock" >&2; exit 1; }
if (runtime_preview_acquire_transition_lock "$fixture_lock") >/dev/null 2>&1; then
  echo "transition lock accepted a concurrent lifecycle operation" >&2
  exit 1
fi
kill "$lock_holder_pid"
wait "$lock_holder_pid" >/dev/null 2>&1 || true
lock_holder_pid=""
(runtime_preview_acquire_transition_lock "$fixture_lock")
ln -s "$fixture_lock" "$env_root/transition-link"
if (runtime_preview_acquire_transition_lock "$env_root/transition-link") >/dev/null 2>&1; then
  echo "transition lock accepted a symlink" >&2
  exit 1
fi

runtime_preview_assert_run_root "$run_root" "$source_sha"
runtime_preview_assert_env_file "$env_file"
token=abcdefghijklmnopqrstuvwxyzABCDEFGH0123456789_-x
token_fingerprint="$(printf '%s' "$token" | sha256sum | awk '{print $1}')"
runtime_preview_write_attestation intent
runtime_preview_verify_attestation intent
runtime_preview_write_attestation provisioning "$token_fingerprint"
runtime_preview_verify_attestation provisioning

install -d -o root -g root -m 0700 "$run_root/easypanel-code"
existing_env="$run_root/easypanel-code/.env"
install -o root -g root -m 0600 "$env_file" "$existing_env"
runtime_preview_remove_attested_env "$env_file" "$existing_env"
[[ ! -e "$existing_env" && ! -L "$existing_env" ]] || {
  echo "attested env cleanup left a residual secret file" >&2
  exit 1
}
runtime_preview_atomic_install_env "$env_file" "$existing_env"
runtime_preview_atomic_install_env "$env_file" "$existing_env"
printf 'APPSELL_SOURCE_SHA=0000000000000000000000000000000000000000\n' >"$existing_env"
chmod 0600 "$existing_env"
if runtime_preview_atomic_install_env "$env_file" "$existing_env" >/dev/null 2>&1; then
  echo "resume accepted a divergent pre-existing EasyPanel env" >&2
  exit 1
fi
install -o root -g root -m 0600 "$env_file" "$existing_env"

compose_content="$(<"$run_root/config/easypanel/compose.runtime-preview.yaml")"$'\n'
mock_response="$(jq -cn \
  --arg token "$token" --arg content "$compose_content" \
  '{json:{projectName:"sell-autonomia",name:"runtime-preview",type:"compose",
    createDotEnv:false,primaryDomainId:null,env:"",token:$token,
    source:{type:"inline",content:$content}}}')"
runtime_preview_api_inspect() { printf '%s' "$mock_response"; }
runtime_preview_verify_live_record yes

public_domain_id=domain-runtime-preview-77
mock_response="$(jq -cn \
  --arg token "$token" --arg content "$compose_content" --arg domain_id "$public_domain_id" \
  '{json:{projectName:"sell-autonomia",name:"runtime-preview",type:"compose",
    createDotEnv:false,primaryDomainId:$domain_id,env:"",token:$token,
    source:{type:"inline",content:$content}}}')"
runtime_preview_verify_public_domain() { printf '%s' "$public_domain_id"; }
runtime_preview_verify_live_record yes
runtime_preview_verify_public_domain() { printf '%s' other-domain; }
if runtime_preview_verify_live_record yes >/dev/null 2>&1; then
  echo "live ownership accepted a mismatched verified public domain" >&2
  exit 1
fi

mock_response="$(jq -cn --arg token 'ZYXWVUTSRQPONMLKJIHGFEDCBA9876543210_-changed' \
  --arg content "$compose_content" \
  '{json:{projectName:"sell-autonomia",name:"runtime-preview",type:"compose",
    createDotEnv:false,primaryDomainId:null,env:"",token:$token,
    source:{type:"inline",content:$content}}}')"
if runtime_preview_verify_live_record yes >/dev/null 2>&1; then
  echo "live ownership accepted a replaced EasyPanel token" >&2
  exit 1
fi

mock_response="$(jq -cn --arg token "$token" \
  '{json:{projectName:"sell-autonomia",name:"runtime-preview",type:"compose",
    createDotEnv:false,primaryDomainId:null,env:"",token:$token,source:{type:"none"}}}')"
runtime_preview_verify_live_record no
if runtime_preview_verify_live_record yes >/dev/null 2>&1; then
  echo "provisioning record without inline source passed the inline gate" >&2
  exit 1
fi

attestation="$run_root/easypanel-runtime-preview.attestation.json"
before="$(sha256sum "$attestation" | awk '{print $1}')"
jq() { return 42; }
if runtime_preview_write_attestation stopped "$token_fingerprint" >/dev/null 2>&1; then
  echo "attestation writer ignored a jq failure" >&2
  exit 1
fi
unset -f jq
[[ "$(sha256sum "$attestation" | awk '{print $1}')" == "$before" ]] || {
  echo "failed attestation write replaced the previous proof" >&2
  exit 1
}

compose_was_called=0
docker() {
  if [[ "$1" == "ps" ]]; then return 0; fi
  if [[ "$1 $2" == "network inspect" ]]; then return 1; fi
  if [[ "$1 $2" == "network ls" ]]; then return 0; fi
  if [[ "$1" == "compose" ]]; then compose_was_called=1; return 42; fi
  return 2
}
if runtime_preview_compose_down >/dev/null 2>&1; then
  echo "Compose-down helper ignored docker compose failure" >&2
  exit 1
fi
[[ "$compose_was_called" == "1" ]] || {
  echo "Compose-down failure fixture did not reach docker compose" >&2
  exit 1
}
unset -f docker

compose_was_called=0
docker() {
  if [[ "$1" == "ps" ]]; then return 42; fi
  if [[ "$1" == "compose" ]]; then compose_was_called=1; return 0; fi
  return 2
}
if runtime_preview_compose_down >/dev/null 2>&1; then
  echo "Compose-down helper accepted a failed pre-removal inventory" >&2
  exit 1
fi
[[ "$compose_was_called" == "0" ]] || {
  echo "Compose down ran without a valid ownership inventory" >&2
  exit 1
}
unset -f docker

chmod 0770 "$run_root"
if runtime_preview_assert_run_root "$run_root" "$source_sha" >/dev/null 2>&1; then
  echo "group-writable run root passed the ownership gate" >&2
  exit 1
fi
chmod 0700 "$run_root"

echo "EasyPanel runtime-preview attestation and rollback helpers: passed"
