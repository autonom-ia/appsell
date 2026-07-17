#!/usr/bin/env bash
set -euo pipefail

for name in \
  APPSELL_MIGRATOR_DATABASE_URL APPSELL_DEMO_DATABASE_URL \
  APPSELL_CONTEXT_SIGNING_KEY_HEX APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX \
  APPSELL_BOOTSTRAP_EXPECTED_ISSUER APPSELL_BOOTSTRAP_EXPECTED_AUDIENCE \
  APPSELL_BOOTSTRAP_EXPECTED_CLIENT_ID APPSELL_IDENTITY_ISSUER; do
  if [[ -z "${!name:-}" ]]; then
    echo "$name is required" >&2
    exit 2
  fi
done

unset PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER PGPASSWORD PGPASSFILE
unset PGSERVICE PGSERVICEFILE PGOPTIONS PGAPPNAME PGSSLMODE PGSSLROOTCERT PGSSLCRL

alembic -c apps/api/alembic.ini upgrade head
python -m appsell_api.configure_context_key

APPSELL_DEMO_ENABLED=true \
APPSELL_DEMO_SUPERADMIN_ENABLED=true \
APPSELL_API_ENVIRONMENT=ci \
python -m appsell_api.reset_demo_tenant --confirm-demo-key autonomia-demo-v1

read -r identity_admin_id identity_buyer_id < <(python - "$APPSELL_IDENTITY_ISSUER" <<'PY'
import sys
from uuid import NAMESPACE_URL, uuid5

issuer = sys.argv[1]
print(uuid5(NAMESPACE_URL, f"{issuer}\0demo-admin"), uuid5(NAMESPACE_URL, f"{issuer}\0demo-user"))
PY
)

psql "$APPSELL_DEMO_DATABASE_URL" --set=ON_ERROR_STOP=1 \
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

echo "Domain bootstrap completed for the isolated Issue #66 preview."
