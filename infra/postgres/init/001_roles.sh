#!/usr/bin/env bash
set -euo pipefail

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${APPSELL_MIGRATOR_PASSWORD:?APPSELL_MIGRATOR_PASSWORD is required}"
: "${APPSELL_RUNTIME_PASSWORD:?APPSELL_RUNTIME_PASSWORD is required}"

psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  --set=db_name="$POSTGRES_DB" \
  --set=migrator_secret="$APPSELL_MIGRATOR_PASSWORD" \
  --set=runtime_secret="$APPSELL_RUNTIME_PASSWORD" <<'SQL'
select 'create role appsell_owner nologin nosuperuser nocreatedb nocreaterole noinherit nobypassrls'
where not exists (select 1 from pg_roles where rolname = 'appsell_owner')\gexec

select 'create role appsell_migrator login nosuperuser nocreatedb nocreaterole inherit nobypassrls'
where not exists (select 1 from pg_roles where rolname = 'appsell_migrator')\gexec

select 'create role appsell_runtime login nosuperuser nocreatedb nocreaterole noinherit nobypassrls'
where not exists (select 1 from pg_roles where rolname = 'appsell_runtime')\gexec

-- Conciliação (#147): só lê, por uma função. Nasce sem login; ganha login
-- abaixo quando APPSELL_RECONCILER_PASSWORD existe. A migration 0062 concede o
-- execute na função se este papel existir.
select 'create role appsell_reconciler nologin nosuperuser nocreatedb nocreaterole noinherit nobypassrls'
where not exists (select 1 from pg_roles where rolname = 'appsell_reconciler')\gexec

alter role appsell_owner nologin nosuperuser nocreatedb nocreaterole noinherit nobypassrls;
alter role appsell_migrator login nosuperuser nocreatedb nocreaterole inherit nobypassrls;
alter role appsell_runtime login nosuperuser nocreatedb nocreaterole noinherit nobypassrls;
alter role appsell_reconciler nosuperuser nocreatedb nocreaterole noinherit nobypassrls;
alter role appsell_reconciler set default_transaction_read_only = on;
alter role appsell_migrator password :'migrator_secret';
alter role appsell_runtime password :'runtime_secret';
grant appsell_owner to appsell_migrator;
alter database :"db_name" owner to appsell_owner;
revoke connect on database :"db_name" from public;
grant connect on database :"db_name" to appsell_migrator, appsell_runtime, appsell_reconciler;
SQL

if [[ -n "${APPSELL_RECONCILER_PASSWORD:-}" ]]; then
  psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    --set=reconciler_secret="$APPSELL_RECONCILER_PASSWORD" <<'SQL'
alter role appsell_reconciler login password :'reconciler_secret';
SQL
fi
