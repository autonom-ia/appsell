#!/usr/bin/env bash
set -euo pipefail

project="appsell_pg_${RANDOM}_$$"
port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
api_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
superuser_password="$(openssl rand -hex 24)"
migrator_password="$(openssl rand -hex 24)"
runtime_password="$(openssl rand -hex 24)"
context_key="$(openssl rand -hex 32)"
bootstrap_key="$(openssl rand -hex 32)"

cleanup() {
  docker compose -p "$project" down --rmi local --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

export APPSELL_POSTGRES_PORT="$port"
export APPSELL_API_PORT="$api_port"
export APPSELL_POSTGRES_SUPERUSER_PASSWORD="$superuser_password"
export APPSELL_MIGRATOR_PASSWORD="$migrator_password"
export APPSELL_RUNTIME_PASSWORD="$runtime_password"

docker compose -p "$project" up -d --wait postgres

export APPSELL_MIGRATOR_DATABASE_URL="postgresql+psycopg://appsell_migrator:${migrator_password}@127.0.0.1:${port}/appsell"
uv run --project apps/api alembic -c apps/api/alembic.ini upgrade head
uv run --project apps/api alembic -c apps/api/alembic.ini upgrade head
export APPSELL_CONTEXT_SIGNING_KEY_HEX="$context_key"
export APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX="$context_key"
export APPSELL_BOOTSTRAP_EXPECTED_ISSUER="https://identity.dev.autonomia.site"
export APPSELL_BOOTSTRAP_EXPECTED_AUDIENCE="appsell-web-dev"
export APPSELL_BOOTSTRAP_EXPECTED_CLIENT_ID="appsell-web-dev"
export APPSELL_BOOTSTRAP_ENABLED="false"
if uv run --project apps/api python -m appsell_api.configure_context_key 2>/dev/null; then
  echo "configure_context_key accepted reused signing keys" >&2
  exit 1
fi
export APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX="$bootstrap_key"
uv run --project apps/api python -m appsell_api.configure_context_key
export APPSELL_BOOTSTRAP_ENABLED="true"
uv run --project apps/api python -m appsell_api.configure_context_key
uv run --project apps/api python -m appsell_api.configure_context_key

export APPSELL_TEST_POSTGRES_ADMIN_URL="postgresql://postgres:${superuser_password}@127.0.0.1:${port}/appsell"
export APPSELL_TEST_POSTGRES_RUNTIME_URL="postgresql://appsell_runtime:${runtime_password}@127.0.0.1:${port}/appsell"
export APPSELL_TEST_CONTEXT_KEY_HEX="$context_key"
export APPSELL_TEST_BOOTSTRAP_KEY_HEX="$bootstrap_key"
uv run --project apps/api pytest apps/api/tests/test_postgres_foundation.py
if uv run --project apps/api python -m appsell_api.configure_context_key 2>/dev/null; then
  echo "configure_context_key re-enabled a consumed bootstrap" >&2
  exit 1
fi

docker compose -p "$project" up -d --build --wait api
curl --fail --silent "http://127.0.0.1:${api_port}/health/live"
curl --fail --silent "http://127.0.0.1:${api_port}/health/ready"
