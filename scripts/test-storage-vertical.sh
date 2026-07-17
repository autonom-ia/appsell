#!/usr/bin/env bash
set -euo pipefail

project="appsell_storage_${RANDOM}_$$"
postgres_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
storage_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
superuser_secret="$(openssl rand -hex 24)"
migrator_secret="$(openssl rand -hex 24)"
runtime_secret="$(openssl rand -hex 24)"
context_key="$(openssl rand -hex 32)"
bootstrap_key="$(openssl rand -hex 32)"
storage_access="contract-$(openssl rand -hex 8)"
storage_secret="$(openssl rand -hex 32)"

cleanup() {
  docker compose -p "$project" --profile storage down --volumes --remove-orphans \
    >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

export APPSELL_POSTGRES_PORT="$postgres_port"
export APPSELL_POSTGRES_SUPERUSER_PASSWORD="$superuser_secret"
export APPSELL_MIGRATOR_PASSWORD="$migrator_secret"
export APPSELL_RUNTIME_PASSWORD="$runtime_secret"
export APPSELL_STORAGE_S3_PORT="$storage_port"
export APPSELL_STORAGE_CONTROL_ACCESS_KEY="$storage_access"
export APPSELL_STORAGE_CONTROL_SECRET_KEY="$storage_secret"
export APPSELL_STORAGE_ALLOWED_ORIGIN="http://localhost:3000"
export APPSELL_STORAGE_BUCKETS="appsell-quarantine,appsell-ready"

docker compose -p "$project" --profile storage up -d --wait postgres seaweedfs
docker compose -p "$project" exec -T postgres psql -U postgres -d appsell \
  -v ON_ERROR_STOP=1 \
  -c "comment on database appsell is 'appsell-demo-reset-target-v1'"

export APPSELL_MIGRATOR_DATABASE_URL="postgresql+psycopg://appsell_migrator:${migrator_secret}@127.0.0.1:${postgres_port}/appsell"
uv run --project apps/api alembic -c apps/api/alembic.ini upgrade head

export APPSELL_CONTEXT_SIGNING_KEY_HEX="$context_key"
export APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX="$bootstrap_key"
export APPSELL_BOOTSTRAP_EXPECTED_ISSUER="https://identity.storage-vertical.test.autonomia.site"
export APPSELL_BOOTSTRAP_EXPECTED_AUDIENCE="appsell-storage-ci"
export APPSELL_BOOTSTRAP_EXPECTED_CLIENT_ID="appsell-storage-ci"
export APPSELL_BOOTSTRAP_ENABLED="true"
uv run --project apps/api python -m appsell_api.configure_context_key

export APPSELL_TEST_POSTGRES_ADMIN_URL="postgresql://postgres:${superuser_secret}@127.0.0.1:${postgres_port}/appsell"
export APPSELL_TEST_POSTGRES_RUNTIME_URL="postgresql://appsell_runtime:${runtime_secret}@127.0.0.1:${postgres_port}/appsell"
export APPSELL_TEST_CONTEXT_KEY_HEX="$context_key"
export APPSELL_TEST_S3_ENDPOINT="http://127.0.0.1:${storage_port}"
export APPSELL_TEST_S3_ACCESS_KEY="$storage_access"
export APPSELL_TEST_S3_SECRET_KEY="$storage_secret"

uv run --project apps/api pytest -ra apps/api/tests/test_storage_s3_contract.py

# The contract above creates both buckets and applies their exact versioning,
# CORS, and anonymous-deny controls. Audit the browser-facing endpoint only
# after that setup, from this external host runner rather than the API network.
export APPSELL_API_ENVIRONMENT="ci"
export APPSELL_API_STORAGE_PROVIDER="s3"
export APPSELL_API_STORAGE_INTERNAL_ENDPOINT="$APPSELL_TEST_S3_ENDPOINT"
export APPSELL_API_STORAGE_PUBLIC_ENDPOINT="$APPSELL_TEST_S3_ENDPOINT"
export APPSELL_API_STORAGE_QUARANTINE_BUCKET="appsell-quarantine"
export APPSELL_API_STORAGE_READY_BUCKET="appsell-ready"
export APPSELL_API_STORAGE_CONTROL_ACCESS_KEY="$storage_access"
export APPSELL_API_STORAGE_CONTROL_SECRET_KEY="$storage_secret"
export APPSELL_API_STORAGE_ALLOWED_ORIGIN="$APPSELL_STORAGE_ALLOWED_ORIGIN"
export APPSELL_API_MALWARE_SCANNER_PROVIDER="deterministic"
export APPSELL_API_PDF_SANDBOX_URL="http://pdf-sandbox.internal:8081/normalize"
audit_output="$(uv run --project apps/api python -m appsell_api.storage_public_audit)"
AUDIT_OUTPUT="$audit_output" python3 -c \
  'import json,os; result=json.loads(os.environ["AUDIT_OUTPUT"]); assert result["status"] == "ok" and result["anonymous_access"] == "denied"'
printf '%s\n' "$audit_output"
unset APPSELL_API_ENVIRONMENT APPSELL_API_STORAGE_PROVIDER \
  APPSELL_API_STORAGE_INTERNAL_ENDPOINT APPSELL_API_STORAGE_PUBLIC_ENDPOINT \
  APPSELL_API_STORAGE_QUARANTINE_BUCKET APPSELL_API_STORAGE_READY_BUCKET \
  APPSELL_API_STORAGE_CONTROL_ACCESS_KEY APPSELL_API_STORAGE_CONTROL_SECRET_KEY \
  APPSELL_API_STORAGE_ALLOWED_ORIGIN APPSELL_API_MALWARE_SCANNER_PROVIDER \
  APPSELL_API_PDF_SANDBOX_URL

uv run --project apps/api pytest -ra \
  apps/api/tests/test_storage_upload_publication_vertical.py
