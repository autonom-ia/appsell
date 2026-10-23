#!/usr/bin/env bash
set -euo pipefail

generated_digest() {
  {
    find output/pdf/codex -type f -print
    printf '%s\n' \
      demo/content/codex-na-pratica/manifest.json \
      lib/demo-codex-assets.json
  } | sort | xargs shasum -a 256
}

generated_before="$(generated_digest)"
uv run --project apps/api python scripts/generate-demo-codex-pdfs.py
generated_after="$(generated_digest)"
if [[ "$generated_before" != "$generated_after" ]]; then
  echo "demo course generator changed committed artifacts" >&2
  diff -u <(printf '%s\n' "$generated_before") <(printf '%s\n' "$generated_after") || true
  exit 1
fi

project="appsell_pg_${RANDOM}_$$"
port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
api_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
superuser_secret="$(openssl rand -hex 24)"
migrator_secret="$(openssl rand -hex 24)"
runtime_secret="$(openssl rand -hex 24)"
reconciler_secret="$(openssl rand -hex 24)"
context_key="$(openssl rand -hex 32)"
bootstrap_key="$(openssl rand -hex 32)"
internal_key_b64="$(openssl rand -base64 32)"
service_credential_b64="$(openssl rand -base64 32)"
webhook_secret_b64="$(openssl rand -base64 32)"
webhook_bff_session_b64="$(openssl rand -base64 32)"
webhook_bff_csrf_b64="$(openssl rand -base64 32)"
webhook_bff_vault_b64="$(openssl rand -base64 32)"
next_env_snapshot="$(mktemp -t appsell-next-env.XXXXXX)"
tsconfig_snapshot="$(mktemp -t appsell-tsconfig.XXXXXX)"
cp -- next-env.d.ts "$next_env_snapshot"
cp -- tsconfig.json "$tsconfig_snapshot"

restore_test_generated_files() {
  cp -- "$next_env_snapshot" next-env.d.ts
  cp -- "$tsconfig_snapshot" tsconfig.json
}

cleanup() {
  local original_status=$?
  restore_test_generated_files || original_status=1
  docker compose -p "$project" down --rmi local --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -f -- "$next_env_snapshot" "$tsconfig_snapshot" || original_status=1
  return "$original_status"
}
trap cleanup EXIT

export APPSELL_POSTGRES_PORT="$port"
export APPSELL_API_PORT="$api_port"
export APPSELL_POSTGRES_SUPERUSER_PASSWORD="$superuser_secret"
export APPSELL_MIGRATOR_PASSWORD="$migrator_secret"
export APPSELL_RUNTIME_PASSWORD="$runtime_secret"
# O 001_roles.sh dá login ao appsell_reconciler quando esta variável existe (#459).
export APPSELL_RECONCILER_PASSWORD="$reconciler_secret"
export APPSELL_INTERNAL_ISSUER="appsell-bff-test"
export APPSELL_INTERNAL_AUDIENCE="appsell-api-test"
export APPSELL_IDENTITY_ISSUER="https://identity.test.autonomia.site"
export APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON="[{\"id\":\"test-v1\",\"key\":\"${internal_key_b64}\",\"active\":true}]"
APPSELL_API_CONTEXT_HMAC_KEY_B64="$(printf '%s' "$context_key" | xxd -r -p | base64)"
export APPSELL_API_CONTEXT_HMAC_KEY_B64
export APPSELL_SERVICE_CREDENTIAL_B64="$service_credential_b64"
export APPSELL_API_WEBHOOK_LOCAL_KEYRING_B64_JSON="[{\"reference\":\"checkout-demo-v1\",\"key\":\"${webhook_secret_b64}\"}]"
export APPSELL_TEST_WEBHOOK_SECRET_B64="$webhook_secret_b64"
export APPSELL_TEST_WEBHOOK_SESSION_KEY_B64="$webhook_bff_session_b64"
export APPSELL_TEST_WEBHOOK_CSRF_KEY_B64="$webhook_bff_csrf_b64"
export APPSELL_TEST_WEBHOOK_VAULT_KEY_B64="$webhook_bff_vault_b64"
export APPSELL_TEST_COMPOSE_PROJECT="$project"
webhook_forbidden_hashes="$(python3 - "$webhook_bff_session_b64" "$webhook_bff_csrf_b64" "$webhook_bff_vault_b64" <<'PY'
import base64
import hashlib
import json
import sys
print(json.dumps([hashlib.sha256(base64.b64decode(value)).hexdigest() for value in sys.argv[1:]]))
PY
)"
export APPSELL_API_WEBHOOK_FORBIDDEN_KEY_SHA256_JSON="$webhook_forbidden_hashes"

if [[ "$webhook_secret_b64" == "$internal_key_b64" || "$webhook_secret_b64" == "$service_credential_b64" \
  || "$webhook_secret_b64" == "$webhook_bff_session_b64" \
  || "$webhook_secret_b64" == "$webhook_bff_csrf_b64" \
  || "$webhook_secret_b64" == "$webhook_bff_vault_b64" ]]; then
  echo "webhook signing secret must be independent from internal API credentials" >&2
  exit 1
fi

docker compose -p "$project" up -d --wait postgres
postgres_ready="false"
for _ in $(seq 1 30); do
  if docker compose -p "$project" exec -T postgres psql -U postgres -d appsell \
    -v ON_ERROR_STOP=1 -Atc "select 1" >/dev/null 2>&1; then
    postgres_ready="true"
    break
  fi
  sleep 1
done
if [[ "$postgres_ready" != "true" ]]; then
  echo "PostgreSQL did not accept a query after the compose healthcheck" >&2
  docker compose -p "$project" ps >&2 || true
  exit 1
fi
docker compose -p "$project" exec -T postgres psql -U postgres -d appsell \
  -v ON_ERROR_STOP=1 -c "comment on database appsell is 'appsell-demo-reset-target-v1'"

export APPSELL_MIGRATOR_DATABASE_URL="postgresql+psycopg://appsell_migrator:${migrator_secret}@127.0.0.1:${port}/appsell"
uv run --project apps/api alembic -c apps/api/alembic.ini upgrade 0004_product_publication
docker compose -p "$project" exec -T postgres psql -U postgres -d appsell -v ON_ERROR_STOP=1 -c "
  insert into identity_user(id,identity_issuer,identity_sub,email_normalized,email_verified)
    values('61000000-0000-4000-8000-000000000001','https://identity.upgrade.test','upgrade-user','upgrade@example.test',true);
  insert into seller_organization(id,slug,display_name,status)
    values('62000000-0000-4000-8000-000000000001','upgrade-v1','Upgrade V1','active');
  insert into seller_membership(seller_organization_id,identity_user_id,role,status)
    values('62000000-0000-4000-8000-000000000001','61000000-0000-4000-8000-000000000001','seller_owner','active');
  insert into catalog_product(seller_organization_id,id,slug,title,description,status)
    values('62000000-0000-4000-8000-000000000001','63000000-0000-4000-8000-000000000001','upgrade-product','Upgrade product','V1 bytes','draft');
  insert into course(seller_organization_id,id,title,description,status,draft_version)
    values('62000000-0000-4000-8000-000000000001','64000000-0000-4000-8000-000000000001','Upgrade course','V1 course','draft',1);
  insert into product_course(seller_organization_id,catalog_product_id,course_id,position)
    values('62000000-0000-4000-8000-000000000001','63000000-0000-4000-8000-000000000001','64000000-0000-4000-8000-000000000001',0);
  insert into offer(seller_organization_id,id,catalog_product_id,title,summary,status,version)
    values('62000000-0000-4000-8000-000000000001','65000000-0000-4000-8000-000000000001','63000000-0000-4000-8000-000000000001','Upgrade offer','V1 offer','draft',1);
  with payload as (
    select '{\"schema_version\":1,\"seller_organization_id\":\"62000000-0000-4000-8000-000000000001\",\"product\":{\"id\":\"63000000-0000-4000-8000-000000000001\",\"slug\":\"upgrade-product\",\"title\":\"Upgrade product\",\"description\":\"V1 bytes\"},\"offer\":{\"id\":\"65000000-0000-4000-8000-000000000001\",\"title\":\"Upgrade offer\",\"summary\":\"V1 offer\",\"billing_authority\":\"external_unconfigured\"},\"courses\":[{\"id\":\"64000000-0000-4000-8000-000000000001\",\"title\":\"Upgrade course\",\"description\":\"V1 course\",\"position\":0}]}'::jsonb snapshot
  ) insert into publication(seller_organization_id,id,catalog_product_id,version,schema_version,source_sha256,snapshot,actor_identity_user_id)
    select '62000000-0000-4000-8000-000000000001','66000000-0000-4000-8000-000000000001','63000000-0000-4000-8000-000000000001',1,1,
      encode(digest(convert_to(snapshot::text,'UTF8'),'sha256'),'hex'),snapshot,'61000000-0000-4000-8000-000000000001' from payload;
  update catalog_product set status='published',published_snapshot_id='66000000-0000-4000-8000-000000000001'
    where seller_organization_id='62000000-0000-4000-8000-000000000001' and id='63000000-0000-4000-8000-000000000001';
"
upgrade_before="$(docker compose -p "$project" exec -T postgres psql -U postgres -d appsell -Atc "
  select product.published_snapshot_id||'|'||publication.source_sha256||'|'||encode(convert_to(publication.snapshot::text,'UTF8'),'base64')
  from catalog_product product join publication on publication.seller_organization_id=product.seller_organization_id
    and publication.id=product.published_snapshot_id
  where product.id='63000000-0000-4000-8000-000000000001'
")"
uv run --project apps/api alembic -c apps/api/alembic.ini upgrade 0011_public_storefront
upgrade_0011="$(docker compose -p "$project" exec -T postgres psql -U postgres -d appsell -Atc "
  select product.published_snapshot_id||'|'||publication.source_sha256||'|'||encode(convert_to(publication.snapshot::text,'UTF8'),'base64')
  from catalog_product product join publication on publication.seller_organization_id=product.seller_organization_id
    and publication.id=product.published_snapshot_id
  where product.id='63000000-0000-4000-8000-000000000001'
")"
if [[ "$upgrade_before" != "$upgrade_0011" ]]; then
  echo "0004 to 0011 changed the v1 publication pointer, hash, or canonical bytes" >&2
  exit 1
fi
uv run --project apps/api alembic -c apps/api/alembic.ini upgrade head
upgrade_after="$(docker compose -p "$project" exec -T postgres psql -U postgres -d appsell -Atc "
  select product.published_snapshot_id||'|'||publication.source_sha256||'|'||encode(convert_to(publication.snapshot::text,'UTF8'),'base64')
  from catalog_product product join publication on publication.seller_organization_id=product.seller_organization_id
    and publication.id=product.published_snapshot_id
  where product.id='63000000-0000-4000-8000-000000000001'
")"
if [[ -z "$upgrade_before" || "$upgrade_before" != "$upgrade_after" ]]; then
  echo "0011 to 0012 changed the v1 publication pointer, hash, or canonical bytes" >&2
  exit 1
fi
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

export APPSELL_TEST_POSTGRES_ADMIN_URL="postgresql://postgres:${superuser_secret}@127.0.0.1:${port}/appsell"
export APPSELL_TEST_POSTGRES_RUNTIME_URL="postgresql://appsell_runtime:${runtime_secret}@127.0.0.1:${port}/appsell"
export APPSELL_TEST_POSTGRES_RECONCILER_URL="postgresql://appsell_reconciler:${reconciler_secret}@127.0.0.1:${port}/appsell"
export APPSELL_TEST_CONTEXT_KEY_HEX="$context_key"
export APPSELL_TEST_BOOTSTRAP_KEY_HEX="$bootstrap_key"
# Aqui o Postgres existe: um teste que pula deixou de provar o que devia, e a
# sessão reprova (apps/api/tests/exige_postgres.py, #450).
export APPSELL_REQUIRE_POSTGRES="1"
# A pasta inteira, para que teste de banco novo entre por existir (#450, #459).
# Cada --ignore roda em outra chamada, e scripts/ci/tests confere isso:
# - demo_checkout, internal_requests e asset_worker_runner, logo abaixo, em
#   processos próprios;
# - os dois de S3 real, no test-storage-vertical.sh (cert-storage).
uv run --project apps/api pytest apps/api/tests \
  --ignore=apps/api/tests/test_api_demo_checkout.py \
  --ignore=apps/api/tests/test_internal_requests.py \
  --ignore=apps/api/tests/test_asset_worker_runner.py \
  --ignore=apps/api/tests/test_storage_s3_contract.py \
  --ignore=apps/api/tests/test_storage_upload_publication_vertical.py
uv run --project apps/api pytest apps/api/tests/test_api_demo_checkout.py
uv run --project apps/api pytest \
  apps/api/tests/test_internal_requests.py \
  apps/api/tests/test_asset_worker_runner.py
if uv run --project apps/api python -m appsell_api.configure_context_key 2>/dev/null; then
  echo "configure_context_key re-enabled a consumed bootstrap" >&2
  exit 1
fi

APPSELL_DEMO_ENABLED="true" \
APPSELL_DEMO_SUPERADMIN_ENABLED="true" \
NODE_ENV="test" \
APP_ENV="ci" \
APPSELL_API_ENVIRONMENT="ci" \
APPSELL_API_IDENTITY_ISSUER="$APPSELL_IDENTITY_ISSUER" \
APPSELL_DEMO_DATABASE_URL="$APPSELL_TEST_POSTGRES_ADMIN_URL" \
uv run --project apps/api python -m appsell_api.reset_demo_tenant \
  --confirm-demo-key autonomia-demo-v1

docker compose -p "$project" up -d --build --wait api
curl --fail --silent "http://127.0.0.1:${api_port}/health/live"
curl --fail --silent "http://127.0.0.1:${api_port}/health/ready"
APPSELL_TEST_DOMAIN_API_URL="http://127.0.0.1:${api_port}" \
APPSELL_LOCAL_IDENTITY_EMAIL="creator@example.test" \
pnpm exec vitest run --no-file-parallelism \
  tests/bff-api-onboarding.test.ts \
  tests/bff-superadmin-inspection.test.ts \
  tests/bff-webhook-ingress.test.ts
restore_test_generated_files
git diff --exit-code -- next-env.d.ts tsconfig.json
