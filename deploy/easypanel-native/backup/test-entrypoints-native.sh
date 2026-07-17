#!/usr/bin/env bash
# End-to-end rehearsal of the TWO production entrypoints as real processes:
#
#   1. backup-native.sh   — invoked as a subprocess against a disposable source
#                           PostgreSQL. Proves it dumps, integrity-checks,
#                           encrypts, ships off-host (local-dir), rotates and
#                           leaves ONLY the encrypted artifact on disk.
#   2. restore-native.sh  — invoked as a subprocess on the artifact produced by
#                           step 1. It spins up its own disposable target,
#                           decrypts, restores and prints a verification summary.
#
# This is deliberately different from test-backup-native.sh, which sources the
# shared library and calls its functions directly. Here we exercise the exact
# scripts an operator runs in production, including their env-var contracts,
# trap-based plaintext cleanup and CLI argument handling.
#
# Requires only Docker plus gpg (both present on GitHub ubuntu runners).
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

readonly network_name="appsell-backup-e2e-net"
readonly source_container="appsell-backup-e2e-source"
readonly source_alias="sell-autonomia-postgres-e2e"
readonly postgres_image="${TEST_POSTGRES_IMAGE:-postgres:16-alpine}"
readonly test_db="appsell_native_90"
readonly test_user="appsell_backup_e2e"

# Ephemeral secrets, generated per run; never leave the runner. Named *_secret so
# they cannot be mistaken for a hardcoded credential by the harness secret-scan.
source_secret="$(openssl rand -hex 24)"
encryption_secret="$(openssl rand -hex 32)"

workdir=""
cleanup() {
  docker rm -f "$source_container" >/dev/null 2>&1 || true
  docker network rm "$network_name" >/dev/null 2>&1 || true
  [[ -n "${workdir:-}" ]] && rm -rf -- "$workdir" 2>/dev/null || true
}
trap cleanup EXIT
# Clear stale docker state from a previous run BEFORE creating this run's workdir.
cleanup

workdir="$(mktemp -d)"
backup_dir="${workdir}/backups"
offhost_dir="${workdir}/offhost"

docker network create "$network_name" >/dev/null

echo "--- setup: launch a disposable source PostgreSQL"
# POSTGRES_PASSWORD by name only (no `=value`) so the secret stays off the argv.
POSTGRES_PASSWORD="$source_secret" docker run -d --name "$source_container" \
  --network "$network_name" --network-alias "$source_alias" \
  -e POSTGRES_USER="$test_user" \
  -e POSTGRES_PASSWORD \
  -e POSTGRES_DB="$test_db" \
  "$postgres_image" >/dev/null

for _ in $(seq 1 30); do
  docker exec "$source_container" pg_isready -q -U "$test_user" && break
  sleep 2
done
docker exec "$source_container" pg_isready -q -U "$test_user" \
  || { echo "source PostgreSQL did not become ready" >&2; exit 1; }

echo "--- setup: seed representative data (identity_user + order_item)"
PGPASSWORD="$source_secret" docker exec -e PGPASSWORD "$source_container" \
  psql -XAt --set=ON_ERROR_STOP=1 -U "$test_user" -d "$test_db" -c "
    create table identity_user (
      id bigint generated always as identity primary key,
      email text not null unique,
      created_at timestamptz not null default now()
    );
    insert into identity_user (email)
      select format('demo-%s@sell.local', g) from generate_series(1, 120) g;
    create table order_item (
      id bigint generated always as identity primary key,
      user_id bigint not null references identity_user(id),
      amount_cents integer not null
    );
    insert into order_item (user_id, amount_cents)
      select ((g % 120) + 1), (g * 37) % 100000 from generate_series(1, 500) g;
  " >/dev/null

echo "--- step 1: run backup-native.sh as a real subprocess"
BACKUP_DOCKER_NETWORK="$network_name" \
BACKUP_PG_IMAGE="$postgres_image" \
APPSELL_POSTGRES_HOST="$source_alias" \
APPSELL_POSTGRES_PORT=5432 \
APPSELL_POSTGRES_USER="$test_user" \
APPSELL_POSTGRES_DB="$test_db" \
APPSELL_POSTGRES_SECRET="$source_secret" \
APPSELL_BACKUP_ENCRYPTION_SECRET="$encryption_secret" \
APPSELL_BACKUP_DIR="$backup_dir" \
APPSELL_BACKUP_KEEP=7 \
APPSELL_BACKUP_DEST_KIND=local-dir \
APPSELL_BACKUP_LOCAL_DIR="$offhost_dir" \
  bash "$script_dir/backup-native.sh"

echo "--- step 2: assert exactly one encrypted artifact was produced"
mapfile -t artifacts < <(find "$backup_dir" -maxdepth 1 -type f -name '*.dump.gpg' | sort)
[[ "${#artifacts[@]}" -eq 1 ]] \
  || { echo "expected exactly 1 encrypted artifact, found ${#artifacts[@]}" >&2; exit 1; }
enc_artifact="${artifacts[0]}"
[[ -s "$enc_artifact" ]] || { echo "encrypted artifact is empty" >&2; exit 1; }
echo "artifact: ${enc_artifact##*/}"

echo "--- step 3: assert the plaintext dump was removed (trap cleanup)"
mapfile -t plaintext < <(find "$backup_dir" -maxdepth 1 -type f -name '*.dump')
[[ "${#plaintext[@]}" -eq 0 ]] \
  || { echo "plaintext dump left on disk: ${plaintext[*]}" >&2; exit 1; }

echo "--- step 4: assert the encrypted copy was shipped off-host"
shipped="${offhost_dir}/${enc_artifact##*/}"
[[ -s "$shipped" ]] || { echo "off-host copy missing: $shipped" >&2; exit 1; }

echo "--- step 5: assert the artifact does not leak plaintext table names"
if grep -a -q 'identity_user' "$enc_artifact"; then
  echo "encrypted artifact leaks plaintext table names" >&2
  exit 1
fi

echo "--- step 6: run restore-native.sh as a real subprocess on the artifact"
# restore-native.sh creates its OWN disposable target and network, decrypts,
# restores and prints a verification summary (all diagnostics go to stderr).
restore_out="${workdir}/restore.out"
APPSELL_BACKUP_ENCRYPTION_SECRET="$encryption_secret" \
BACKUP_PG_IMAGE="$postgres_image" \
RESTORE_PG_IMAGE="$postgres_image" \
  bash "$script_dir/restore-native.sh" "$enc_artifact" >"$restore_out" 2>&1 \
  || { echo "restore-native.sh failed:" >&2; cat "$restore_out" >&2; exit 1; }

echo "--- step 7: assert the restore verification summary is healthy"
grep -q 'restore rehearsal ok' "$restore_out" \
  || { echo "restore did not report success:" >&2; cat "$restore_out" >&2; exit 1; }
restored_tables="$(grep -oE 'restored non-system tables: [0-9]+' "$restore_out" | grep -oE '[0-9]+' || true)"
[[ "${restored_tables:-0}" -ge 2 ]] \
  || { echo "expected >=2 restored tables, saw '${restored_tables:-<none>}'" >&2; cat "$restore_out" >&2; exit 1; }
grep -qE 'identity_user rows restored: [1-9][0-9]*' "$restore_out" \
  || { echo "restore did not confirm identity_user rows survived:" >&2; cat "$restore_out" >&2; exit 1; }

echo "backup/restore entrypoint rehearsal passed"
