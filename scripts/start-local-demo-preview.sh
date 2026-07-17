#!/usr/bin/env bash
set -euo pipefail

web_port="${1:-3200}"
if [[ ! "$web_port" =~ ^[0-9]+$ ]] || ((web_port < 1 || web_port > 65535)); then
  echo "uso: $0 [porta entre 1 e 65535]" >&2
  exit 2
fi
project="appsell_preview_${RANDOM}_$$"
next_dist_dir=".next-preview-${project}"
postgres_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
api_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
redis_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
storage_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
redis_dir="$(mktemp -d "${TMPDIR:-/tmp}/appsell-preview-redis.XXXXXX")"
generated_backup="$(mktemp -d "${TMPDIR:-/tmp}/appsell-preview-generated.XXXXXX")"

superuser_secret="$(openssl rand -hex 24)"
migrator_secret="$(openssl rand -hex 24)"
runtime_secret="$(openssl rand -hex 24)"
context_key="$(openssl rand -hex 32)"
bootstrap_key="$(openssl rand -hex 32)"
internal_key_b64="$(openssl rand -base64 32)"
service_credential_b64="$(openssl rand -base64 32)"
session_key_b64="$(openssl rand -base64 32)"
csrf_key_b64="$(openssl rand -base64 32)"
vault_key_b64="$(openssl rand -base64 32)"
redis_secret="$(openssl rand -hex 32)"
storage_access_key="preview-$(openssl rand -hex 12)"
storage_secret_key="$(openssl rand -hex 32)"
next_pid=""
redis_pid=""
cleanup_started="false"

cp next-env.d.ts "$generated_backup/next-env.d.ts"
cp tsconfig.json "$generated_backup/tsconfig.json"

restore_generated_config() {
  cp "$generated_backup/next-env.d.ts" next-env.d.ts
  cp "$generated_backup/tsconfig.json" tsconfig.json
}

cleanup() {
  if [[ "$cleanup_started" == "true" ]]; then return; fi
  cleanup_started="true"
  trap - EXIT INT TERM
  if [[ -n "$next_pid" ]]; then kill "$next_pid" >/dev/null 2>&1 || true; fi
  if [[ -n "$redis_pid" ]]; then kill "$redis_pid" >/dev/null 2>&1 || true; fi
  wait "$next_pid" >/dev/null 2>&1 || true
  wait "$redis_pid" >/dev/null 2>&1 || true
  docker compose -p "$project" down --rmi local --volumes --remove-orphans >/dev/null 2>&1 || true
  restore_generated_config
  rm -rf -- "$redis_dir" "$generated_backup" "$next_dist_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

export APPSELL_POSTGRES_PORT="$postgres_port"
export APPSELL_API_PORT="$api_port"
export APPSELL_POSTGRES_SUPERUSER_PASSWORD="$superuser_secret"
export APPSELL_MIGRATOR_PASSWORD="$migrator_secret"
export APPSELL_RUNTIME_PASSWORD="$runtime_secret"
export APPSELL_INTERNAL_ISSUER="appsell-bff-preview"
export APPSELL_INTERNAL_AUDIENCE="appsell-api-preview"
export APPSELL_IDENTITY_ISSUER="https://identity.preview.autonomia.site"
export APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON="[{\"id\":\"preview-v1\",\"key\":\"${internal_key_b64}\",\"active\":true}]"
export APPSELL_API_CONTEXT_HMAC_KEY_B64="$(printf '%s' "$context_key" | xxd -r -p | base64)"
export APPSELL_SERVICE_CREDENTIAL_B64="$service_credential_b64"
# Docker Compose maps the generic variables above into the API container.
# Host-side API CLIs read the APPSELL_API_ namespace directly, so provide the
# same complete authentication tuple before invoking any of them.
export APPSELL_API_INTERNAL_ISSUER="$APPSELL_INTERNAL_ISSUER"
export APPSELL_API_INTERNAL_AUDIENCE="$APPSELL_INTERNAL_AUDIENCE"
export APPSELL_API_INTERNAL_HMAC_KEYRING_B64_JSON="$APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON"
export APPSELL_API_SERVICE_CREDENTIAL_B64="$APPSELL_SERVICE_CREDENTIAL_B64"
origin="http://localhost:${web_port}"

docker compose -p "$project" up -d --wait postgres
docker compose -p "$project" exec -T postgres psql -U postgres -d appsell \
  -v ON_ERROR_STOP=1 -c "comment on database appsell is 'appsell-demo-reset-target-v1'" >/dev/null

export APPSELL_MIGRATOR_DATABASE_URL="postgresql+psycopg://appsell_migrator:${migrator_secret}@127.0.0.1:${postgres_port}/appsell"
uv run --project apps/api alembic -c apps/api/alembic.ini upgrade head
export APPSELL_CONTEXT_SIGNING_KEY_HEX="$context_key"
export APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX="$bootstrap_key"
export APPSELL_BOOTSTRAP_EXPECTED_ISSUER="https://identity.preview.autonomia.site"
export APPSELL_BOOTSTRAP_EXPECTED_AUDIENCE="appsell-web-preview"
export APPSELL_BOOTSTRAP_EXPECTED_CLIENT_ID="appsell-web-preview"
export APPSELL_BOOTSTRAP_ENABLED="false"
uv run --project apps/api python -m appsell_api.configure_context_key

export APPSELL_DEMO_ENABLED="true"
export APPSELL_DEMO_SUPERADMIN_ENABLED="true"
export NODE_ENV="development"
export APP_ENV="local"
export APPSELL_API_ENVIRONMENT="local"
export APPSELL_API_IDENTITY_ISSUER="$APPSELL_IDENTITY_ISSUER"
export APPSELL_DEMO_DATABASE_URL="postgresql://postgres:${superuser_secret}@127.0.0.1:${postgres_port}/appsell"
uv run --project apps/api python -m appsell_api.reset_demo_tenant \
  --confirm-demo-key autonomia-demo-v1

export APPSELL_STORAGE_S3_PORT="$storage_port"
export APPSELL_STORAGE_CONTROL_ACCESS_KEY="$storage_access_key"
export APPSELL_STORAGE_CONTROL_SECRET_KEY="$storage_secret_key"
export APPSELL_STORAGE_ALLOWED_ORIGIN="$origin"
export APPSELL_STORAGE_BUCKETS="appsell-quarantine,appsell-ready"
export APPSELL_API_STORAGE_PROVIDER="s3"
export APPSELL_API_STORAGE_INTERNAL_ENDPOINT="http://seaweedfs:8333"
export APPSELL_API_STORAGE_PUBLIC_ENDPOINT="http://127.0.0.1:${storage_port}"
export APPSELL_API_STORAGE_REGION="us-east-1"
export APPSELL_API_STORAGE_QUARANTINE_BUCKET="appsell-quarantine"
export APPSELL_API_STORAGE_READY_BUCKET="appsell-ready"
export APPSELL_API_STORAGE_CONTROL_ACCESS_KEY="$storage_access_key"
export APPSELL_API_STORAGE_CONTROL_SECRET_KEY="$storage_secret_key"
export APPSELL_API_STORAGE_ALLOWED_ORIGIN="$origin"
export APPSELL_API_MALWARE_SCANNER_PROVIDER="deterministic"
docker compose -p "$project" --profile storage up -d --wait seaweedfs
if ! docker compose -p "$project" --profile storage up -d --build --wait api; then
  docker compose -p "$project" --profile storage logs --no-color api pdf-sandbox seaweedfs >&2
  exit 1
fi
APPSELL_API_PDF_SANDBOX_SOCKET="/run/appsell-pdf/pdf.sock" \
  uv run --project apps/api --frozen python -m appsell_api.storage_public_audit
redis-server --bind 127.0.0.1 --port "$redis_port" --dir "$redis_dir" \
  --save '' --appendonly no --protected-mode yes --requirepass "$redis_secret" \
  >"$redis_dir/redis.log" 2>&1 &
redis_pid="$!"

export APPSELL_REDIS_URL="redis://:${redis_secret}@127.0.0.1:${redis_port}/0"
export APPSELL_REDIS_PREFIX="appsell:preview:${project}"
export APPSELL_SESSION_HMAC_KEY_B64="$session_key_b64"
export APPSELL_CSRF_HMAC_KEY_B64="$csrf_key_b64"
export APPSELL_PUBLIC_ORIGIN="$origin"
export APPSELL_STORAGE_PUBLIC_ORIGIN="http://127.0.0.1:${storage_port}"
export APPSELL_VAULT_LOCAL_KEYRING_B64_JSON="[{\"id\":\"preview-v1\",\"key\":\"${vault_key_b64}\",\"active\":true}]"
export APPSELL_DOMAIN_API_URL="http://127.0.0.1:${api_port}"
export APPSELL_DEMO_ENABLED="true"
export APPSELL_DEMO_SUPERADMIN_ENABLED="true"
export APPSELL_NEXT_DIST_DIR="$next_dist_dir"
export NEXT_TELEMETRY_DISABLED="1"

pnpm dev --hostname 127.0.0.1 --port "$web_port" &
next_pid="$!"
preview_ready="false"
for _ in $(seq 1 120); do
  if curl --silent --fail --max-time 1 "$origin" >/dev/null; then
    preview_ready="true"
    break
  fi
  if ! kill -0 "$next_pid" >/dev/null 2>&1; then
    wait "$next_pid"
    exit $?
  fi
  sleep 0.1
done
if [[ "$preview_ready" != "true" ]]; then
  echo "preview local não ficou pronto em 12 segundos" >&2
  exit 1
fi
restore_generated_config
echo "AppSell demo local: $origin"
echo "Use os botões 'Entrar como admin demo' e 'Entrar como aluno demo'."
echo "Pressione Ctrl+C para remover toda a infraestrutura descartável."
wait "$next_pid"
