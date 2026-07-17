#!/usr/bin/env bash
# Rehearses the full backup/restore pipeline against disposable PostgreSQL
# containers, end to end: seed a source database, dump it, verify integrity,
# encrypt, ship off-host (local-dir), decrypt the shipped copy, restore into a
# SECOND database, and prove the data matches byte-for-byte on a checksum.
#
# This is the CI counterpart of deploy/easypanel-native/test-bootstrap-native.sh
# and requires only Docker plus gpg (both present on GitHub ubuntu runners).
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/easypanel-native/backup/lib-backup.sh
source "$script_dir/lib-backup.sh"

readonly network_name="appsell-backup-test-net"
readonly source_container="appsell-backup-test-source"
readonly target_container="appsell-backup-test-target"
readonly source_alias="backup-source"
readonly target_alias="backup-target"
readonly postgres_image="${TEST_POSTGRES_IMAGE:-postgres:16-alpine}"
readonly test_db="appsell_native_90"
readonly test_user="appsell_backup_test"

# All secrets below are ephemeral, generated per run, and never leave the runner.
source_secret="$(openssl rand -hex 24)"
target_secret="$(openssl rand -hex 24)"
export APPSELL_BACKUP_ENCRYPTION_SECRET="$(openssl rand -hex 32)"

export BACKUP_DOCKER_NETWORK="$network_name"
export BACKUP_PG_IMAGE="$postgres_image"

cleanup() {
  docker rm -f "$source_container" "$target_container" >/dev/null 2>&1 || true
  docker network rm "$network_name" >/dev/null 2>&1 || true
  [[ -n "${workdir:-}" ]] && rm -rf -- "$workdir" 2>/dev/null || true
}
trap cleanup EXIT
# Clear stale docker state from a previous run BEFORE creating this run's workdir
# (the initial cleanup must not delete the fresh temp dir we are about to make).
cleanup

workdir="$(mktemp -d)"
offhost_dir="${workdir}/offhost"
dump_file="${workdir}/source.dump"
enc_file="${dump_file}.gpg"
restored_plain="${workdir}/restored-from-offhost.dump"

docker network create "$network_name" >/dev/null

launch_postgres() {
  local container="$1" alias="$2" secret="$3"
  docker run -d --name "$container" \
    --network "$network_name" --network-alias "$alias" \
    -e POSTGRES_USER="$test_user" \
    -e POSTGRES_PASSWORD="$secret" \
    -e POSTGRES_DB="$test_db" \
    "$postgres_image" >/dev/null
}

wait_ready() {
  local container="$1"
  local _
  for _ in $(seq 1 30); do
    docker exec "$container" pg_isready -q -U "$test_user" && return 0
    sleep 2
  done
  docker exec "$container" pg_isready -q -U "$test_user"
}

echo "--- setup: launch disposable source and target PostgreSQL"
launch_postgres "$source_container" "$source_alias" "$source_secret"
launch_postgres "$target_container" "$target_alias" "$target_secret"
wait_ready "$source_container"
wait_ready "$target_container"

echo "--- step 1: seed representative data into the source database"
docker exec -e PGPASSWORD="$source_secret" "$source_container" \
  psql -XAt --set=ON_ERROR_STOP=1 -U "$test_user" -d "$test_db" -c "
    create table identity_user (
      id bigint generated always as identity primary key,
      email text not null unique,
      created_at timestamptz not null default now()
    );
    insert into identity_user (email)
      select format('demo-%s@sell.local', g) from generate_series(1, 250) g;
    create table order_item (
      id bigint generated always as identity primary key,
      user_id bigint not null references identity_user(id),
      amount_cents integer not null
    );
    insert into order_item (user_id, amount_cents)
      select ((g % 250) + 1), (g * 37) % 100000 from generate_series(1, 1000) g;
  " >/dev/null

# A content fingerprint that is independent of physical layout: ordered row
# digests folded into one value. Must match exactly after the round trip.
fingerprint_sql="
  select md5(string_agg(row_digest, '' order by row_digest)) from (
    select md5(u.id::text || u.email) as row_digest from identity_user u
    union all
    select md5(o.id::text || o.user_id::text || o.amount_cents::text) from order_item o
  ) s"

source_fingerprint="$(pg_scalar "$source_alias" 5432 "$test_user" "$test_db" \
  "$source_secret" "$fingerprint_sql")"
[[ -n "$source_fingerprint" ]] || { echo "failed to compute source fingerprint" >&2; exit 1; }
echo "source fingerprint: ${source_fingerprint}"

echo "--- step 2: dump the source (custom format)"
pg_dump_to_file "$source_alias" 5432 "$test_user" "$test_db" \
  "$source_secret" "$dump_file"
[[ -s "$dump_file" ]] || { echo "dump is empty" >&2; exit 1; }

echo "--- step 3: integrity check on the dump"
pg_verify_dump "$dump_file"

echo "--- step 4: encrypt (gpg AES256)"
encrypt_file "$dump_file" "$enc_file"
# The encrypted artifact must not contain the plaintext table names.
if grep -a -q 'identity_user' "$enc_file"; then
  echo "encrypted artifact leaks plaintext table names" >&2
  exit 1
fi

echo "--- step 5: ship off-host (local-dir) and confirm the artifact landed"
export APPSELL_BACKUP_DEST_KIND="local-dir"
export APPSELL_BACKUP_LOCAL_DIR="$offhost_dir"
ship_offhost "$enc_file"
shipped="${offhost_dir}/${enc_file##*/}"
[[ -s "$shipped" ]] || { echo "off-host copy missing: $shipped" >&2; exit 1; }

echo "--- step 6: decrypt the OFF-HOST copy (proves the shipped file is usable)"
decrypt_file "$shipped" "$restored_plain"

echo "--- step 7: restore into the SECOND database"
pg_restore_from_file "$target_alias" 5432 "$test_user" "$test_db" \
  "$target_secret" "$restored_plain"

echo "--- step 8: compare — target fingerprint must equal source fingerprint"
target_fingerprint="$(pg_scalar "$target_alias" 5432 "$test_user" "$test_db" \
  "$target_secret" "$fingerprint_sql")"
echo "target fingerprint: ${target_fingerprint}"
if [[ "$source_fingerprint" != "$target_fingerprint" ]]; then
  echo "FINGERPRINT MISMATCH: restore does not match source" >&2
  exit 1
fi

echo "--- step 9: negative control — a wrong passphrase must fail to decrypt"
wrong_secret="$(openssl rand -hex 32)"
if gpg --batch --yes --quiet --no-symkey-cache --pinentry-mode loopback --passphrase-fd 3 \
    --decrypt --output "${workdir}/should-not-exist.dump" "$shipped" \
    3<<<"$wrong_secret" 2>/dev/null; then
  echo "decryption with a wrong passphrase must fail but succeeded" >&2
  exit 1
fi

echo "backup/restore rehearsal passed"
