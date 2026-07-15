#!/usr/bin/env bash
set -euo pipefail

[[ "${CONFIRM_RUNTIME_PREVIEW_RETIRE:-}" == "appsell-preview-77-retire" ]] || {
  echo "set CONFIRM_RUNTIME_PREVIEW_RETIRE=appsell-preview-77-retire" >&2
  exit 2
}

mapfile -t postgres_containers < <(
  docker ps --filter label=com.docker.swarm.service.name=sell-autonomia_postgres \
    --format '{{.ID}}'
)
[[ ${#postgres_containers[@]} -eq 1 ]] || {
  echo "expected exactly one healthy sell-autonomia_postgres task" >&2
  exit 1
}
container="${postgres_containers[0]}"
postgres_user="$(docker exec "$container" printenv POSTGRES_USER)"
postgres_database="$(docker exec "$container" printenv POSTGRES_DB)"
[[ -n "$postgres_user" && -n "$postgres_database" ]] || {
  echo "exclusive PostgreSQL identity is unavailable" >&2
  exit 1
}

role_state="$(docker exec "$container" psql -X -U "$postgres_user" \
  -d "$postgres_database" -F '|' -Atc "
    select rolname,coalesce(shobj_description(oid,'pg_authid'),'')
    from pg_roles
    where rolname in ('appsell_preview_77_migrator','appsell_preview_77_runtime')
    order by rolname
  ")"
if [[ -z "$role_state" ]]; then
  echo "runtime-preview login credentials already retired"
  exit 0
fi
expected_state=$'appsell_preview_77_migrator|appsell-runtime-preview-77-v1\nappsell_preview_77_runtime|appsell-runtime-preview-77-v1'
[[ "$role_state" == "$expected_state" ]] || {
  echo "refusing to retire unmarked or partial runtime-preview roles" >&2
  exit 1
}

database_marker="$(docker exec "$container" psql -X -U "$postgres_user" \
  -d "$postgres_database" -Atc "
    select coalesce(shobj_description(oid,'pg_database'),'')
    from pg_database where datname='appsell_preview_77'
  ")"
[[ "$database_marker" == "appsell-demo-reset-target-v1" ]] || {
  echo "refusing to retire credentials for an unmarked database" >&2
  exit 1
}

active_sessions="$(docker exec "$container" psql -X -U "$postgres_user" \
  -d "$postgres_database" -Atc "
    select count(*) from pg_stat_activity
    where usename in ('appsell_preview_77_migrator','appsell_preview_77_runtime')
  ")"
[[ "$active_sessions" == "0" ]] || {
  echo "remove runtime-preview containers before retiring database credentials" >&2
  exit 1
}

docker exec -i "$container" psql -X -U "$postgres_user" -d "$postgres_database" \
  --set=ON_ERROR_STOP=1 <<'SQL' >/dev/null
begin;
revoke connect on database appsell_preview_77
  from appsell_preview_77_migrator,appsell_preview_77_runtime;
revoke appsell_owner from appsell_preview_77_migrator;
revoke appsell_runtime from appsell_preview_77_runtime;
drop role appsell_preview_77_migrator;
drop role appsell_preview_77_runtime;
commit;
SQL

echo "runtime-preview login credentials retired; database and canonical roles preserved"
