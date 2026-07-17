#!/usr/bin/env bash
set -euo pipefail

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${APPSELL_MIGRATOR_PASSWORD:?APPSELL_MIGRATOR_PASSWORD is required}"
: "${APPSELL_RUNTIME_PASSWORD:?APPSELL_RUNTIME_PASSWORD is required}"

if [[ "$POSTGRES_USER" != "postgres" || "$POSTGRES_DB" != "appsell_preview_66" || "${PGPORT:-}" != "15432" ]]; then
  echo "Refusing to mark an unexpected database" >&2
  exit 2
fi

psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  --set=migrator_secret="$APPSELL_MIGRATOR_PASSWORD" \
  --set=runtime_secret="$APPSELL_RUNTIME_PASSWORD" <<'SQL'
select 'create role appsell_owner nologin nosuperuser nocreatedb nocreaterole noinherit nobypassrls'
where not exists (select 1 from pg_roles where rolname = 'appsell_owner')\gexec
select 'create role appsell_migrator login nosuperuser nocreatedb nocreaterole inherit nobypassrls'
where not exists (select 1 from pg_roles where rolname = 'appsell_migrator')\gexec
select 'create role appsell_runtime login nosuperuser nocreatedb nocreaterole noinherit nobypassrls'
where not exists (select 1 from pg_roles where rolname = 'appsell_runtime')\gexec
alter role appsell_owner nologin nosuperuser nocreatedb nocreaterole noinherit nobypassrls;
alter role appsell_migrator login nosuperuser nocreatedb nocreaterole inherit nobypassrls;
alter role appsell_runtime login nosuperuser nocreatedb nocreaterole noinherit nobypassrls;
alter role appsell_migrator password :'migrator_secret';
alter role appsell_runtime password :'runtime_secret';
grant appsell_owner to appsell_migrator;
alter database appsell_preview_66 owner to appsell_owner;
revoke connect on database appsell_preview_66 from public;
grant connect on database appsell_preview_66 to appsell_migrator, appsell_runtime;
comment on database appsell_preview_66 is 'appsell-demo-reset-target-v1';
SQL
