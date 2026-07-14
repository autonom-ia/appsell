#!/usr/bin/env bash
set -euo pipefail

# libpq environment variables must never override the disposable loopback DSN.
# The outer PostgreSQL wrapper also strips them; keep this guard here because
# this script performs privileged role/bootstrap commands before app startup.
unset PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER PGPASSWORD PGPASSFILE
unset PGSERVICE PGSERVICEFILE PGOPTIONS PGAPPNAME PGSSLMODE PGSSLROOTCERT PGSSLCRL
unset PGCHANNELBINDING

: "${TEST_DATABASE_URL:?TEST_DATABASE_URL is required}"
: "${IDENTITY_TEST_DATABASE_ISOLATED:?IDENTITY_TEST_DATABASE_ISOLATED is required}"
: "${IDENTITY_TEST_DATABASE_ISOLATION_TOKEN:?IDENTITY_TEST_DATABASE_ISOLATION_TOKEN is required}"
: "${E2E_DOMAIN_API_PORT:?E2E_DOMAIN_API_PORT is required}"
: "${APPSELL_MIGRATOR_PASSWORD:?APPSELL_MIGRATOR_PASSWORD is required}"
: "${APPSELL_RUNTIME_PASSWORD:?APPSELL_RUNTIME_PASSWORD is required}"
: "${APPSELL_INTERNAL_ISSUER:?APPSELL_INTERNAL_ISSUER is required}"
: "${APPSELL_INTERNAL_AUDIENCE:?APPSELL_INTERNAL_AUDIENCE is required}"
: "${APPSELL_IDENTITY_ISSUER:?APPSELL_IDENTITY_ISSUER is required}"
: "${APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON:?APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON is required}"
: "${APPSELL_API_CONTEXT_HMAC_KEY_B64:?APPSELL_API_CONTEXT_HMAC_KEY_B64 is required}"
: "${APPSELL_SERVICE_CREDENTIAL_B64:?APPSELL_SERVICE_CREDENTIAL_B64 is required}"
: "${APPSELL_CONTEXT_SIGNING_KEY_HEX:?APPSELL_CONTEXT_SIGNING_KEY_HEX is required}"
: "${APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX:?APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX is required}"

database_port="$(python3 - "$TEST_DATABASE_URL" "$IDENTITY_TEST_DATABASE_ISOLATED" <<'PY'
import re
import sys
from urllib.parse import urlparse

url = urlparse(sys.argv[1])
sentinel = sys.argv[2]
database = url.path.removeprefix("/")
valid = (
    url.scheme == "postgresql"
    and url.hostname == "127.0.0.1"
    and url.username == "postgres"
    and url.password is None
    and url.port is not None
    and not url.params
    and not url.query
    and not url.fragment
    and database == sentinel
    and re.fullmatch(r"identity_oauth_test_[0-9a-f]{32}", sentinel)
)
if not valid:
    raise SystemExit("Domain API E2E requires the wrapper-provided disposable loopback PostgreSQL URL")
print(url.port)
PY
)"

database_marker="$(psql "$TEST_DATABASE_URL" --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  --command "select shobj_description(oid,'pg_database') from pg_database where datname=current_database()")"
if [[ "$database_marker" != "identity-isolated:${IDENTITY_TEST_DATABASE_ISOLATION_TOKEN}" ]]; then
  echo "Domain API E2E database marker does not match the wrapper capability" >&2
  exit 2
fi

export PGHOST="127.0.0.1"
export PGPORT="$database_port"
export POSTGRES_USER="postgres"
export POSTGRES_DB="$IDENTITY_TEST_DATABASE_ISOLATED"
bash infra/postgres/init/001_roles.sh
psql "$TEST_DATABASE_URL" --set=ON_ERROR_STOP=1 \
  -c "comment on database \"$IDENTITY_TEST_DATABASE_ISOLATED\" is 'appsell-demo-reset-target-v1'" >/dev/null
unset PGHOST PGPORT

export APPSELL_MIGRATOR_DATABASE_URL="postgresql+psycopg://appsell_migrator:${APPSELL_MIGRATOR_PASSWORD}@127.0.0.1:${database_port}/${IDENTITY_TEST_DATABASE_ISOLATED}"
uv run --project apps/api --frozen alembic -c apps/api/alembic.ini upgrade head

export APPSELL_API_INTERNAL_ISSUER="$APPSELL_INTERNAL_ISSUER"
export APPSELL_API_INTERNAL_AUDIENCE="$APPSELL_INTERNAL_AUDIENCE"
export APPSELL_API_IDENTITY_ISSUER="$APPSELL_IDENTITY_ISSUER"
export APPSELL_API_INTERNAL_HMAC_KEYRING_B64_JSON="$APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON"
export APPSELL_API_SERVICE_CREDENTIAL_B64="$APPSELL_SERVICE_CREDENTIAL_B64"
export APPSELL_BOOTSTRAP_EXPECTED_ISSUER="$APPSELL_IDENTITY_ISSUER"
export APPSELL_BOOTSTRAP_EXPECTED_AUDIENCE="appsell-web"
export APPSELL_BOOTSTRAP_EXPECTED_CLIENT_ID="appsell-web"
export APPSELL_BOOTSTRAP_ENABLED="false"
uv run --project apps/api --frozen python -m appsell_api.configure_context_key

export APPSELL_DEMO_ENABLED="true"
export APPSELL_API_ENVIRONMENT="ci"
export APPSELL_DEMO_DATABASE_URL="$TEST_DATABASE_URL"
uv run --project apps/api --frozen python -m appsell_api.reset_demo_tenant \
  --confirm-demo-key autonomia-demo-v1

read -r identity_admin_id identity_buyer_id < <(python3 - "$APPSELL_IDENTITY_ISSUER" <<'PY'
import sys
from uuid import NAMESPACE_URL, uuid5

issuer = sys.argv[1]
print(uuid5(NAMESPACE_URL, f"{issuer}\0demo-admin"), uuid5(NAMESPACE_URL, f"{issuer}\0demo-user"))
PY
)
psql "$TEST_DATABASE_URL" --set=ON_ERROR_STOP=1 \
  --set=identity_issuer="$APPSELL_IDENTITY_ISSUER" \
  --set=identity_admin_id="$identity_admin_id" \
  --set=identity_buyer_id="$identity_buyer_id" <<'SQL' >/dev/null
insert into identity_user(id,identity_issuer,identity_sub,email_normalized,email_verified)
values
  (:'identity_admin_id', :'identity_issuer', 'demo-admin', 'admin.demo@autonomia.site', true),
  (:'identity_buyer_id', :'identity_issuer', 'demo-user', 'user.demo@autonomia.site', true)
on conflict(identity_issuer,identity_sub) do update set
  email_normalized=excluded.email_normalized,
  email_verified=true,
  updated_at=clock_timestamp();

insert into seller_organization(id,slug,display_name,status)
values('70000000-0000-4000-8000-000000000099','identity-e2e-foreign','Tenant alheio do E2E','active')
on conflict(id) do nothing;

update seller_membership membership
set identity_user_id=:'identity_admin_id',role='seller_owner',status='active',
  updated_at=clock_timestamp()
from app_private.demo_tenant_registry registry
where registry.seed_key='autonomia-demo-v1'
  and membership.seller_organization_id=registry.seller_organization_id
  and membership.role='seller_owner' and membership.status='active';

insert into buyer_entitlement(
  seller_organization_id,id,buyer_identity_user_id,catalog_product_id,
  publication_id,reference_key,source,status,progress_percent
)
select seller_organization_id,'69000000-0000-4000-8000-000000000010',
  :'identity_buyer_id',catalog_product_id,baseline_publication_id,
  'identity-demo-user-codex-v1','demo_seed','active',0
from app_private.demo_tenant_registry where seed_key='autonomia-demo-v1'
on conflict(seller_organization_id,buyer_identity_user_id,catalog_product_id)
do update set publication_id=excluded.publication_id,status='active',
  progress_percent=0,starts_at=clock_timestamp(),expires_at=null,
  updated_at=clock_timestamp();
SQL

export APPSELL_API_DATABASE_URL="postgresql+asyncpg://appsell_runtime:${APPSELL_RUNTIME_PASSWORD}@127.0.0.1:${database_port}/${IDENTITY_TEST_DATABASE_ISOLATED}"
export APPSELL_API_DATABASE_APPLICATION_NAME="appsell_identity_e2e"
export APPSELL_API_STORAGE_PROVIDER="disabled"
exec uv run --project apps/api --frozen uvicorn appsell_api.main:app \
  --host 127.0.0.1 --port "$E2E_DOMAIN_API_PORT"
