#!/usr/bin/env bash
set -euo pipefail

for name in \
  APPSELL_POSTGRES_HOST APPSELL_POSTGRES_PORT APPSELL_POSTGRES_ADMIN_USER \
  APPSELL_POSTGRES_ADMIN_PASSWORD APPSELL_POSTGRES_DEFAULT_DATABASE \
  APPSELL_DATABASE_NAME \
  APPSELL_MIGRATOR_PASSWORD APPSELL_RUNTIME_PASSWORD \
  APPSELL_CONTEXT_SIGNING_KEY_HEX APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX \
  APPSELL_BOOTSTRAP_EXPECTED_ISSUER APPSELL_BOOTSTRAP_EXPECTED_AUDIENCE \
  APPSELL_BOOTSTRAP_EXPECTED_CLIENT_ID APPSELL_IDENTITY_ISSUER; do
  [[ -n "${!name:-}" ]] || { echo "$name is required" >&2; exit 2; }
done

[[ "$APPSELL_DATABASE_NAME" == "appsell_preview_77" ]] || {
  echo "runtime preview database must be appsell_preview_77" >&2
  exit 2
}
[[ "$APPSELL_POSTGRES_DEFAULT_DATABASE" =~ ^[a-z][a-z0-9_]{0,62}$ ]] || {
  echo "exclusive PostgreSQL default database name is invalid" >&2
  exit 2
}
[[ "$APPSELL_MIGRATOR_PASSWORD" =~ ^[0-9a-f]{48}$ ]] || {
  echo "migrator password must be 48 lowercase hex characters" >&2
  exit 2
}
[[ "$APPSELL_RUNTIME_PASSWORD" =~ ^[0-9a-f]{48}$ ]] || {
  echo "runtime password must be 48 lowercase hex characters" >&2
  exit 2
}

unset PGHOSTADDR PGPASSFILE PGSERVICE PGSERVICEFILE PGOPTIONS PGAPPNAME
export PGHOST="$APPSELL_POSTGRES_HOST"
export PGPORT="$APPSELL_POSTGRES_PORT"
export PGUSER="$APPSELL_POSTGRES_ADMIN_USER"
export PGPASSWORD="$APPSELL_POSTGRES_ADMIN_PASSWORD"
export PGDATABASE=postgres

for _ in $(seq 1 60); do
  pg_isready -q && break
  sleep 2
done
pg_isready -q || { echo "exclusive sell PostgreSQL is not ready" >&2; exit 1; }

unexpected_databases="$(psql -XAt --set=ON_ERROR_STOP=1 \
  --set=database_name="$APPSELL_DATABASE_NAME" \
  --set=default_database="$APPSELL_POSTGRES_DEFAULT_DATABASE" <<'SQL'
select datname from pg_database
where datallowconn and not datistemplate
  and datname not in ('postgres',:'default_database',:'database_name')
order by datname;
SQL
)"
[[ -z "$unexpected_databases" ]] || {
  echo "exclusive sell PostgreSQL contains an unexpected database" >&2
  exit 1
}
for database in postgres "$APPSELL_POSTGRES_DEFAULT_DATABASE"; do
  [[ "$database" == "$APPSELL_DATABASE_NAME" ]] && continue
  user_relations="$(psql -XAt --set=ON_ERROR_STOP=1 --dbname="$database" <<'SQL'
select count(*) from pg_class class
join pg_namespace namespace on namespace.oid=class.relnamespace
where namespace.nspname not in ('pg_catalog','information_schema')
  and namespace.nspname not like 'pg_toast%'
  and class.relkind in ('r','p','v','m','S','f');
SQL
)"
  [[ "$user_relations" == "0" ]] || {
    echo "refusing cluster-wide roles because a non-preview database contains objects" >&2
    exit 1
  }
done

role_marker=appsell-runtime-preview-77-v1
role_state="$(psql -XAt --set=ON_ERROR_STOP=1 --set=role_marker="$role_marker" <<'SQL'
select rolname||'|'||coalesce(shobj_description(oid,'pg_authid'),'')
from pg_roles
where rolname in (
  'appsell_owner','appsell_runtime',
  'appsell_preview_77_migrator','appsell_preview_77_runtime'
)
order by rolname;
SQL
)"
while IFS='|' read -r role_name role_comment; do
  [[ -z "$role_name" || "$role_comment" == "$role_marker" ]] || {
    echo "refusing pre-existing unmarked AppSell role" >&2
    exit 1
  }
done <<<"$role_state"

database_marker="$(psql -XAt --set=ON_ERROR_STOP=1 \
  --set=database_name="$APPSELL_DATABASE_NAME" <<'SQL'
select case
  when not exists(select 1 from pg_database where datname=:'database_name') then 'absent'
  else coalesce((select shobj_description(oid,'pg_database') from pg_database
    where datname=:'database_name'),'unmarked')
end;
SQL
)"
case "$database_marker" in
  absent|appsell-demo-reset-target-v1) ;;
  *) echo "refusing unmarked runtime preview database" >&2; exit 1 ;;
esac

psql -X --set=ON_ERROR_STOP=1 \
  --set=database_name="$APPSELL_DATABASE_NAME" \
  <<'SQL' >/dev/null
\getenv migrator_secret APPSELL_MIGRATOR_PASSWORD
\getenv runtime_secret APPSELL_RUNTIME_PASSWORD
select 'create role appsell_owner nologin nosuperuser nocreatedb nocreaterole noinherit nobypassrls'
where not exists(select 1 from pg_roles where rolname='appsell_owner')\gexec
select 'create role appsell_runtime nologin nosuperuser nocreatedb nocreaterole noinherit nobypassrls'
where not exists(select 1 from pg_roles where rolname='appsell_runtime')\gexec
select 'create role appsell_preview_77_migrator login nosuperuser nocreatedb nocreaterole inherit nobypassrls'
where not exists(select 1 from pg_roles where rolname='appsell_preview_77_migrator')\gexec
select 'create role appsell_preview_77_runtime login nosuperuser nocreatedb nocreaterole inherit nobypassrls'
where not exists(select 1 from pg_roles where rolname='appsell_preview_77_runtime')\gexec
alter role appsell_preview_77_migrator login nosuperuser nocreatedb nocreaterole inherit nobypassrls;
alter role appsell_preview_77_runtime login nosuperuser nocreatedb nocreaterole inherit nobypassrls;
alter role appsell_preview_77_migrator password :'migrator_secret';
alter role appsell_preview_77_runtime password :'runtime_secret';
grant appsell_owner to appsell_preview_77_migrator;
grant appsell_runtime to appsell_preview_77_runtime;
comment on role appsell_owner is 'appsell-runtime-preview-77-v1';
comment on role appsell_runtime is 'appsell-runtime-preview-77-v1';
comment on role appsell_preview_77_migrator is 'appsell-runtime-preview-77-v1';
comment on role appsell_preview_77_runtime is 'appsell-runtime-preview-77-v1';
select format('create database %I owner appsell_owner', :'database_name')
where not exists(select 1 from pg_database where datname=:'database_name')\gexec
select format('alter database %I owner to appsell_owner', :'database_name')\gexec
select format('revoke connect on database %I from public', :'database_name')\gexec
select format(
  'grant connect on database %I to appsell_preview_77_migrator,appsell_preview_77_runtime',
  :'database_name'
)\gexec
select format('comment on database %I is %L', :'database_name','appsell-demo-reset-target-v1')\gexec
SQL

readarray -t database_urls < <(python - <<'PY'
import os
from urllib.parse import quote

host = os.environ["APPSELL_POSTGRES_HOST"]
port = os.environ["APPSELL_POSTGRES_PORT"]
database = os.environ["APPSELL_DATABASE_NAME"]
admin_user = quote(os.environ["APPSELL_POSTGRES_ADMIN_USER"], safe="")
admin_password = quote(os.environ["APPSELL_POSTGRES_ADMIN_PASSWORD"], safe="")
migrator_password = quote(os.environ["APPSELL_MIGRATOR_PASSWORD"], safe="")
print(f"postgresql+psycopg://appsell_preview_77_migrator:{migrator_password}@{host}:{port}/{database}")
print(f"postgresql://{admin_user}:{admin_password}@{host}:{port}/{database}")
PY
)
export APPSELL_MIGRATOR_DATABASE_URL="${database_urls[0]}"
export APPSELL_DEMO_DATABASE_URL="${database_urls[1]}"
export APPSELL_BOOTSTRAP_ENABLED=false
export APPSELL_API_ENVIRONMENT=ci
export APPSELL_API_IDENTITY_ISSUER="$APPSELL_IDENTITY_ISSUER"

alembic -c apps/api/alembic.ini upgrade head
python -m appsell_api.configure_context_key
APPSELL_DEMO_ENABLED=true APPSELL_DEMO_SUPERADMIN_ENABLED=true \
  python -m appsell_api.reset_demo_tenant --confirm-demo-key autonomia-demo-v1

psql --dbname="$APPSELL_DATABASE_NAME" -XAt --set=ON_ERROR_STOP=1 \
  --command="select case when app_private.schema_ready() then 'ready' else 'not-ready' end" \
  | grep -qx ready

echo "runtime-preview domain bootstrap=ready database=appsell_preview_77"
