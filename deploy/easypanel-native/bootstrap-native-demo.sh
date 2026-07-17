#!/usr/bin/env bash
set -euo pipefail

readonly confirm_value="sell-autonomia-native-90"
readonly database_name="appsell_native_90"
readonly migrator_role="appsell_native_90_migrator"
readonly runtime_role="appsell_native_90_runtime"
readonly role_marker="appsell-native-90-v1"
readonly database_marker="appsell-demo-reset-target-v1"
readonly bootstrap_database_marker="appsell-native-90-bootstrap-v1"

lock_directory="/tmp/appsell-native-90-bootstrap.lock"
mkdir "$lock_directory" 2>/dev/null || {
  echo "native bootstrap is already running in this container" >&2
  exit 1
}
trap 'rmdir "$lock_directory" 2>/dev/null || true' EXIT

for name in \
  APPSELL_NATIVE_BOOTSTRAP_CONFIRM \
  APPSELL_POSTGRES_HOST APPSELL_POSTGRES_PORT \
  APPSELL_POSTGRES_ADMIN_USER APPSELL_POSTGRES_ADMIN_PASSWORD \
  APPSELL_POSTGRES_DEFAULT_DATABASE \
  APPSELL_NATIVE_MIGRATOR_PASSWORD APPSELL_NATIVE_RUNTIME_PASSWORD \
  APPSELL_CONTEXT_SIGNING_KEY_HEX APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX \
  APPSELL_BOOTSTRAP_EXPECTED_ISSUER APPSELL_BOOTSTRAP_EXPECTED_AUDIENCE \
  APPSELL_BOOTSTRAP_EXPECTED_CLIENT_ID; do
  [[ -n "${!name:-}" ]] || { echo "$name is required" >&2; exit 2; }
done

[[ "$APPSELL_NATIVE_BOOTSTRAP_CONFIRM" == "$confirm_value" ]] || {
  echo "native bootstrap confirmation is invalid" >&2
  exit 2
}
[[ "$APPSELL_POSTGRES_HOST" == "sell-autonomia_postgres-native-90" \
  && "$APPSELL_POSTGRES_PORT" == "5432" \
  && "$APPSELL_POSTGRES_ADMIN_USER" == "appsell_native_admin" ]] || {
  echo "native bootstrap PostgreSQL target is invalid" >&2
  exit 2
}
[[ "$APPSELL_POSTGRES_DEFAULT_DATABASE" == "appsell_native_admin" ]] || {
  echo "native bootstrap default database is invalid" >&2
  exit 2
}
[[ "$APPSELL_NATIVE_MIGRATOR_PASSWORD" =~ ^[0-9a-f]{48}$ \
  && "$APPSELL_NATIVE_RUNTIME_PASSWORD" =~ ^[0-9a-f]{48}$ \
  && "$APPSELL_NATIVE_MIGRATOR_PASSWORD" != "$APPSELL_NATIVE_RUNTIME_PASSWORD" ]] || {
  echo "native database role passwords are invalid" >&2
  exit 2
}
[[ "$APPSELL_CONTEXT_SIGNING_KEY_HEX" =~ ^[0-9a-f]{64}$ \
  && "$APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX" =~ ^[0-9a-f]{64}$ \
  && "$APPSELL_CONTEXT_SIGNING_KEY_HEX" != "$APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX" ]] || {
  echo "native bootstrap signing keys are invalid" >&2
  exit 2
}
[[ "$APPSELL_BOOTSTRAP_EXPECTED_ISSUER" == "https://identity.native.local" \
  && "$APPSELL_BOOTSTRAP_EXPECTED_AUDIENCE" == "appsell-bootstrap-native-90" \
  && "$APPSELL_BOOTSTRAP_EXPECTED_CLIENT_ID" == "appsell-native-90" ]] || {
  echo "native bootstrap trust policy is invalid" >&2
  exit 2
}

unset PGHOSTADDR PGPASSFILE PGSERVICE PGSERVICEFILE PGSYSCONFDIR PGOPTIONS PGAPPNAME
export PGHOST="$APPSELL_POSTGRES_HOST"
export PGPORT="$APPSELL_POSTGRES_PORT"
export PGUSER="$APPSELL_POSTGRES_ADMIN_USER"
export PGPASSWORD="$APPSELL_POSTGRES_ADMIN_PASSWORD"
export PGDATABASE=postgres

for _ in $(seq 1 60); do
  pg_isready -q && break
  sleep 2
done
pg_isready -q || { echo "exclusive Sell PostgreSQL is not ready" >&2; exit 1; }

# Preflight the full blast radius before the first mutation. This PostgreSQL
# service is exclusive to native-90 and must not contain any other app database.
unexpected_databases="$(psql -XAt --set=ON_ERROR_STOP=1 \
  --set=default_database="$APPSELL_POSTGRES_DEFAULT_DATABASE" \
  --set=native_database="$database_name" <<'SQL'
select datname from pg_database
where datallowconn and not datistemplate
  and datname not in ('postgres', :'default_database', :'native_database')
order by datname;
SQL
)"
[[ -z "$unexpected_databases" ]] || {
  echo "exclusive Sell PostgreSQL contains an unexpected database" >&2
  exit 1
}

unsafe_shared_roles="$(psql -XAt --set=ON_ERROR_STOP=1 <<'SQL'
select rolname from pg_roles
where rolname in ('appsell_owner','appsell_runtime')
  and (
    rolcanlogin or rolsuper or rolcreatedb or rolcreaterole or rolinherit or rolbypassrls
    or coalesce(shobj_description(oid,'pg_authid'),'') <> 'appsell-native-shared-v1'
  )
order by rolname;
SQL
)"
[[ -z "$unsafe_shared_roles" ]] || {
  echo "refusing unsafe or unowned shared AppSell roles" >&2
  exit 1
}

unsafe_native_roles="$(psql -XAt --set=ON_ERROR_STOP=1 \
  --set=migrator_role="$migrator_role" --set=runtime_role="$runtime_role" \
  --set=role_marker="$role_marker" <<'SQL'
select rolname from pg_roles
where rolname in (:'migrator_role', :'runtime_role')
  and (
    not rolcanlogin or rolsuper or rolcreatedb or rolcreaterole or not rolinherit or rolbypassrls
    or coalesce(shobj_description(oid,'pg_authid'),'') <> :'role_marker'
  )
order by rolname;
SQL
)"
[[ -z "$unsafe_native_roles" ]] || {
  echo "refusing pre-existing native roles with drifted ownership" >&2
  exit 1
}

database_state="$(psql -XAt --set=ON_ERROR_STOP=1 \
  --set=database_name="$database_name" <<'SQL'
select case
  when not exists(select 1 from pg_database where datname=:'database_name') then 'absent'
  else coalesce((select shobj_description(oid,'pg_database') from pg_database
    where datname=:'database_name'),'unmarked')
end;
SQL
)"
[[ "$database_state" == "absent" \
  || "$database_state" == "$bootstrap_database_marker" \
  || "$database_state" == "$database_marker" ]] || {
  echo "refusing an unmarked native demo database" >&2
  exit 1
}

if [[ "$database_state" == "$database_marker" ]]; then
  configured_state="$(psql -XAt --set=ON_ERROR_STOP=1 --dbname="$database_name" <<'SQL'
\getenv expected_context_key APPSELL_CONTEXT_SIGNING_KEY_HEX
\getenv expected_bootstrap_key APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX
\getenv expected_issuer APPSELL_BOOTSTRAP_EXPECTED_ISSUER
\getenv expected_audience APPSELL_BOOTSTRAP_EXPECTED_AUDIENCE
\getenv expected_client_id APPSELL_BOOTSTRAP_EXPECTED_CLIENT_ID
select case
  when to_regclass('app_private.context_signing_key') is null
    or to_regclass('app_private.bootstrap_trust_policy') is null then 'missing'
  when (select encode(secret,'hex') from app_private.context_signing_key where singleton)
    <> :'expected_context_key' then 'context-key-drift'
  when (select encode(attestation_secret,'hex') from app_private.bootstrap_trust_policy
    where singleton) <> :'expected_bootstrap_key' then 'bootstrap-key-drift'
  when (select issuer from app_private.bootstrap_trust_policy where singleton)
    <> :'expected_issuer' then 'issuer-drift'
  when (select audience from app_private.bootstrap_trust_policy where singleton)
    <> :'expected_audience' then 'audience-drift'
  when (select client_id from app_private.bootstrap_trust_policy where singleton)
    <> :'expected_client_id' then 'client-drift'
  when (select enabled from app_private.bootstrap_trust_policy where singleton) then 'enabled-drift'
  else 'ready'
end;
SQL
)"
  [[ "$configured_state" == "ready" ]] || {
    echo "refusing native bootstrap configuration drift" >&2
    exit 1
  }
  echo "native bootstrap=already-complete database=appsell_native_90"
  exit 0
fi

psql -X --set=ON_ERROR_STOP=1 \
  --set=database_name="$database_name" \
  --set=migrator_role="$migrator_role" \
  --set=runtime_role="$runtime_role" \
  --set=role_marker="$role_marker" \
  --set=bootstrap_database_marker="$bootstrap_database_marker" <<'SQL' >/dev/null
\getenv migrator_secret APPSELL_NATIVE_MIGRATOR_PASSWORD
\getenv runtime_secret APPSELL_NATIVE_RUNTIME_PASSWORD
select 'create role appsell_owner nologin nosuperuser nocreatedb nocreaterole noinherit nobypassrls'
where not exists(select 1 from pg_roles where rolname='appsell_owner')\gexec
select 'create role appsell_runtime nologin nosuperuser nocreatedb nocreaterole noinherit nobypassrls'
where not exists(select 1 from pg_roles where rolname='appsell_runtime')\gexec
select 'comment on role appsell_owner is ''appsell-native-shared-v1'''
where coalesce((select shobj_description(oid,'pg_authid') from pg_roles
  where rolname='appsell_owner'),'')=''\gexec
select 'comment on role appsell_runtime is ''appsell-native-shared-v1'''
where coalesce((select shobj_description(oid,'pg_authid') from pg_roles
  where rolname='appsell_runtime'),'')=''\gexec
select format(
  'create role %I login nosuperuser nocreatedb nocreaterole inherit nobypassrls', :'migrator_role'
) where not exists(select 1 from pg_roles where rolname=:'migrator_role')\gexec
select format(
  'create role %I login nosuperuser nocreatedb nocreaterole inherit nobypassrls', :'runtime_role'
) where not exists(select 1 from pg_roles where rolname=:'runtime_role')\gexec
select format('alter role %I password %L', :'migrator_role', :'migrator_secret')\gexec
select format('alter role %I password %L', :'runtime_role', :'runtime_secret')\gexec
select format('grant appsell_owner to %I', :'migrator_role')\gexec
select format('grant appsell_runtime to %I', :'runtime_role')\gexec
select format('comment on role %I is %L', :'migrator_role', :'role_marker')\gexec
select format('comment on role %I is %L', :'runtime_role', :'role_marker')\gexec
select format('create database %I owner appsell_owner', :'database_name')
where not exists(select 1 from pg_database where datname=:'database_name')\gexec
select format('alter database %I owner to appsell_owner', :'database_name')\gexec
select format('revoke connect on database %I from public', :'database_name')\gexec
select format(
  'grant connect on database %I to %I, %I',
  :'database_name', :'migrator_role', :'runtime_role'
)\gexec
select format('comment on database %I is %L', :'database_name', :'bootstrap_database_marker')\gexec
SQL

readarray -t database_urls < <(python - <<'PY'
import os
from urllib.parse import quote

host = os.environ["APPSELL_POSTGRES_HOST"]
port = os.environ["APPSELL_POSTGRES_PORT"]
password = quote(os.environ["APPSELL_NATIVE_MIGRATOR_PASSWORD"], safe="")
base = f"appsell_native_90_migrator:{password}@{host}:{port}/appsell_native_90"
print(f"postgresql+psycopg://{base}")
print(f"postgresql://{base}")
PY
)
export APPSELL_MIGRATOR_DATABASE_URL="${database_urls[0]}"
export APPSELL_DEMO_DATABASE_URL="${database_urls[1]}"
export APPSELL_BOOTSTRAP_ENABLED=false
export APPSELL_API_ENVIRONMENT=ci
export APPSELL_API_IDENTITY_ISSUER="https://identity.native.local"

alembic -c apps/api/alembic.ini upgrade head
python -m appsell_api.configure_context_key
(
  unset PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER PGPASSWORD PGPASSFILE
  unset PGSERVICE PGSERVICEFILE PGSYSCONFDIR PGOPTIONS PGAPPNAME
  NODE_ENV=test APP_ENV=ci \
    APPSELL_DEMO_REMOTE_RESET_SCOPE=appsell-native-90 \
    APPSELL_DEMO_ENABLED=true APPSELL_DEMO_SUPERADMIN_ENABLED=true \
    python -m appsell_api.reset_demo_tenant --confirm-demo-key autonomia-demo-v1
)

psql --dbname="$database_name" -XAt --set=ON_ERROR_STOP=1 \
  --command="select case when app_private.schema_ready() then 'ready' else 'not-ready' end" \
  | grep -qx ready

# The final marker is deliberately the last mutation. A failed migration,
# configuration or seed remains resumable and can never be mistaken for a
# completed bootstrap by a restarted EasyPanel task.
psql --dbname=postgres -X --set=ON_ERROR_STOP=1 \
  --set=database_name="$database_name" --set=database_marker="$database_marker" <<'SQL' >/dev/null
select format('comment on database %I is %L', :'database_name', :'database_marker')\gexec
SQL

echo "native bootstrap=ready database=appsell_native_90"
