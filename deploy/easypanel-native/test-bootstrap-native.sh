#!/usr/bin/env bash
# Rehearses deploy/easypanel-native/bootstrap-native-demo.sh against a
# disposable PostgreSQL container, covering the marker lifecycle end to end:
# fresh database, idempotent re-run, resume from the legacy in-progress marker,
# resume from a mid-reset crash, configuration drift and an unmarked database.
# Requires Docker and the pre-built bootstrap image (BOOTSTRAP_IMAGE).
set -euo pipefail

readonly network_name="appsell-native-bootstrap-test"
readonly postgres_container="appsell-native-test-postgres"
readonly postgres_alias="sell-autonomia_postgres-native-90"
readonly postgres_image="${TEST_POSTGRES_IMAGE:-postgres:16-alpine}"
readonly bootstrap_image="${BOOTSTRAP_IMAGE:?BOOTSTRAP_IMAGE is required}"
readonly complete_marker="appsell-native-90-complete-v1"
readonly legacy_marker="appsell-native-90-bootstrap-v1"
readonly reset_target_marker="appsell-demo-reset-target-v1"

admin_password="$(openssl rand -hex 24)"
migrator_password="$(openssl rand -hex 24)"
runtime_password="$(openssl rand -hex 24)"
context_key="$(openssl rand -hex 32)"
attestation_key="$(openssl rand -hex 32)"

cleanup() {
  docker rm -f "$postgres_container" >/dev/null 2>&1 || true
  docker network rm "$network_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup

docker network create "$network_name" >/dev/null
docker run -d --name "$postgres_container" \
  --network "$network_name" --network-alias "$postgres_alias" \
  -e POSTGRES_USER=appsell_native_admin \
  -e POSTGRES_PASSWORD="$admin_password" \
  -e POSTGRES_DB=appsell_native_admin \
  "$postgres_image" >/dev/null

admin_psql() {
  docker exec -e PGPASSWORD="$admin_password" "$postgres_container" \
    psql -XAt --set=ON_ERROR_STOP=1 -U appsell_native_admin -d "$1" -c "$2"
}

for _ in $(seq 1 30); do
  docker exec "$postgres_container" pg_isready -q -U appsell_native_admin && break
  sleep 2
done
docker exec "$postgres_container" pg_isready -q -U appsell_native_admin

run_bootstrap() {
  docker run --rm --network "$network_name" \
    -e APPSELL_NATIVE_BOOTSTRAP_CONFIRM=sell-autonomia-native-90 \
    -e APPSELL_POSTGRES_HOST="$postgres_alias" \
    -e APPSELL_POSTGRES_PORT=5432 \
    -e APPSELL_POSTGRES_ADMIN_USER=appsell_native_admin \
    -e APPSELL_POSTGRES_ADMIN_PASSWORD="$admin_password" \
    -e APPSELL_POSTGRES_DEFAULT_DATABASE=appsell_native_admin \
    -e APPSELL_NATIVE_MIGRATOR_PASSWORD="$migrator_password" \
    -e APPSELL_NATIVE_RUNTIME_PASSWORD="$runtime_password" \
    -e APPSELL_CONTEXT_SIGNING_KEY_HEX="${1:-$context_key}" \
    -e APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX="$attestation_key" \
    -e APPSELL_BOOTSTRAP_EXPECTED_ISSUER=https://identity.native.local \
    -e APPSELL_BOOTSTRAP_EXPECTED_AUDIENCE=appsell-bootstrap-native-90 \
    -e APPSELL_BOOTSTRAP_EXPECTED_CLIENT_ID=appsell-native-90 \
    "$bootstrap_image" /opt/app/deploy/easypanel-native/bootstrap-native-demo.sh
}

database_marker_now() {
  admin_psql postgres \
    "select coalesce(shobj_description(oid,'pg_database'),'unmarked') from pg_database where datname='appsell_native_90'"
}

expect_marker() {
  local actual
  actual="$(database_marker_now)"
  [[ "$actual" == "$1" ]] || {
    echo "expected database marker '$1', found '$actual'" >&2
    exit 1
  }
}

echo "--- scenario 1: fresh database completes and locks the marker"
output="$(run_bootstrap)"
grep -qx "native bootstrap=ready database=appsell_native_90" <<<"$output"
expect_marker "$complete_marker"
admin_psql appsell_native_90 \
  "select case when app_private.schema_ready() then 'ready' else 'not-ready' end" | grep -qx ready
demo_users="$(admin_psql appsell_native_90 'select count(*) from identity_user')"
[[ "$demo_users" -gt 0 ]] || { echo "demo seed produced no identity users" >&2; exit 1; }

echo "--- scenario 2: re-run is idempotent and reports already-complete"
output="$(run_bootstrap)"
grep -qx "native bootstrap=already-complete database=appsell_native_90" <<<"$output"
expect_marker "$complete_marker"

echo "--- scenario 3: context key drift on a complete database is refused"
if output="$(run_bootstrap "$(openssl rand -hex 32)" 2>&1)"; then
  echo "drifted context key must not be accepted" >&2
  exit 1
fi
grep -q "refusing native bootstrap configuration drift" <<<"$output"
expect_marker "$complete_marker"

echo "--- scenario 4: legacy in-progress marker resumes to completion"
admin_psql postgres "comment on database appsell_native_90 is '$legacy_marker'" >/dev/null
output="$(run_bootstrap)"
grep -qx "native bootstrap=ready database=appsell_native_90" <<<"$output"
expect_marker "$complete_marker"

echo "--- scenario 5: mid-reset crash residue resumes to completion"
admin_psql postgres "comment on database appsell_native_90 is '$reset_target_marker'" >/dev/null
output="$(run_bootstrap)"
grep -qx "native bootstrap=ready database=appsell_native_90" <<<"$output"
expect_marker "$complete_marker"

echo "--- scenario 6: an unrecognized marker is refused untouched"
admin_psql postgres "comment on database appsell_native_90 is 'rogue-marker'" >/dev/null
if output="$(run_bootstrap 2>&1)"; then
  echo "an unrecognized database marker must be refused" >&2
  exit 1
fi
grep -q "refusing an unmarked native demo database" <<<"$output"
expect_marker "rogue-marker"
admin_psql postgres "comment on database appsell_native_90 is '$complete_marker'" >/dev/null

echo "native bootstrap rehearsal passed"
