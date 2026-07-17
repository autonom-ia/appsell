#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

docker_endpoint="${DOCKER_HOST:-$(docker context inspect "$(docker context show)" --format '{{.Endpoints.docker.Host}}')}"
if [[ "$docker_endpoint" != unix:///* ]]; then
  printf 'Refusing non-local Docker endpoint for disposable OpenBao test: %s\n' "$docker_endpoint" >&2
  exit 2
fi

project="appsell-transit-test-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}-$$"
compose=(docker compose --file "$repo_root/compose.yaml" --project-name "$project" --profile transit)
port="${APPSELL_OPENBAO_PORT:-}"
if [[ -z "$port" ]]; then
  port="$(node -e 'const n=require("node:net");const s=n.createServer();s.listen(0,"127.0.0.1",()=>{console.log(s.address().port);s.close()})')"
fi
root_token="$(openssl rand -hex 32)"
export APPSELL_OPENBAO_PORT="$port"
export APPSELL_OPENBAO_DEV_ROOT_TOKEN="$root_token"
# Compose validates the complete file even when only the isolated Transit
# profile is selected. These one-run values are never used because no database
# service is started, but prevent accidental reuse of ambient credentials.
export APPSELL_POSTGRES_SUPERUSER_PASSWORD="$(openssl rand -hex 24)"
export APPSELL_MIGRATOR_PASSWORD="$(openssl rand -hex 24)"
export APPSELL_RUNTIME_PASSWORD="$(openssl rand -hex 24)"

cleanup() {
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  unset APPSELL_OPENBAO_DEV_ROOT_TOKEN APPSELL_OPENBAO_TEST_ROOT_TOKEN APPSELL_OPENBAO_TOKEN \
    APPSELL_POSTGRES_SUPERUSER_PASSWORD APPSELL_MIGRATOR_PASSWORD APPSELL_RUNTIME_PASSWORD
}
trap cleanup EXIT

"${compose[@]}" up --detach --wait openbao
address="http://127.0.0.1:${port}"

bao_post() {
  local path="$1"
  local body="${2:-}"
  if [[ -z "$body" ]]; then body='{}'; fi
  printf 'Bootstrapping disposable OpenBao endpoint: %s\n' "$path" >&2
  curl --fail-with-body --silent --show-error \
    --header "content-type: application/json" \
    --header "x-vault-token: ${root_token}" \
    --request POST \
    --data "$body" \
    "${address}/v1/${path}"
}

bao_post "sys/mounts/appsell-transit" '{"type":"transit"}' >/dev/null
bao_post "appsell-transit/config/keys" '{"disable_upsert":true}' >/dev/null
bao_post "appsell-transit/keys/appsell-oauth-vault" \
  '{"type":"aes256-gcm96","derived":false,"exportable":false,"allow_plaintext_backup":false}' >/dev/null

policy_payload="$(jq --raw-input --slurp '{policy:.}' infra/openbao/policies/appsell-transit-runtime.hcl)"
bao_post "sys/policies/acl/appsell-transit-runtime" "$policy_payload" >/dev/null
token_payload='{"policies":["appsell-transit-runtime"],"ttl":"15m","explicit_max_ttl":"15m","renewable":false,"no_parent":true,"no_default_policy":true}'
runtime_response="$(bao_post "auth/token/create-orphan" "$token_payload")"
runtime_token="$(jq --exit-status --raw-output '.auth.client_token' <<<"$runtime_response")"

export APPSELL_OPENBAO_REAL_TEST=1
export APPSELL_OPENBAO_ADDR="$address"
export APPSELL_OPENBAO_TRANSIT_MOUNT=appsell-transit
export APPSELL_OPENBAO_TRANSIT_KEY=appsell-oauth-vault
export APPSELL_OPENBAO_TOKEN="$runtime_token"
export APPSELL_OPENBAO_TEST_ROOT_TOKEN="$root_token"
export APPSELL_OPENBAO_HTTP_TIMEOUT_MS=1000
export NODE_ENV=test

pnpm exec vitest run \
  tests/openbao-transit-provider.test.ts \
  tests/openbao-transit-bff.test.ts \
  tests/openbao-transit-real.test.ts
