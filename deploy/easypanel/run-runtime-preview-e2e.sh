#!/usr/bin/env bash
set -euo pipefail

: "${E2E_RUNTIME_EVIDENCE_DIR:?E2E_RUNTIME_EVIDENCE_DIR is required}"
: "${E2E_RUNTIME_BRIDGE_SOCKET:?E2E_RUNTIME_BRIDGE_SOCKET is required}"
[[ "$E2E_RUNTIME_BRIDGE_SOCKET" == "/transport/sell.sock" ]] || {
  echo "runtime-preview E2E bridge socket drifted" >&2
  exit 1
}

staged="$E2E_RUNTIME_EVIDENCE_DIR"
target=/evidence/current
backup=/evidence/previous
failed=/evidence/failed
proxy_pid=""

cleanup_proxy() {
  if [[ -n "$proxy_pid" ]] && kill -0 "$proxy_pid" 2>/dev/null; then
    kill -TERM "$proxy_pid"
    wait "$proxy_pid" || true
  fi
  proxy_pid=""
}

trap cleanup_proxy EXIT
rm -rf "$staged"
mkdir -p "$staged"

promote_failure() {
  status=$?
  rm -rf "$failed"
  if [[ -d "$staged" ]]; then
    mv "$staged" "$failed"
  fi
  echo "runtime-preview E2E failed; diagnostic evidence preserved in /evidence/failed" >&2
  exit "$status"
}
trap promote_failure ERR

node deploy/easypanel/runtime-preview-loopback-proxy.mjs &
proxy_pid=$!
proxy_ready=0
for _attempt in $(seq 1 30); do
  kill -0 "$proxy_pid" 2>/dev/null || break
  if node -e 'fetch(process.env.E2E_RUNTIME_ORIGIN, {redirect:"manual"}).then(r=>{if(r.status < 200 || r.status >= 400) process.exit(1)}).catch(()=>process.exit(1))'; then
    proxy_ready=1
    break
  fi
  sleep 1
done
[[ "$proxy_ready" == "1" ]] || {
  echo "runtime-preview loopback proxy did not become ready" >&2
  false
}

corepack pnpm exec playwright test --config=playwright.runtime-preview.config.ts
trap - ERR
cleanup_proxy
trap - EXIT

rm -rf "$backup"
rm -rf "$failed"
if [[ -d "$target" ]]; then
  mv "$target" "$backup"
fi
mv "$staged" "$target"
rm -rf "$backup"

echo "runtime-preview E2E evidence promoted to /evidence/current"
