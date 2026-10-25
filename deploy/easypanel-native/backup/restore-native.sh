#!/usr/bin/env bash
# Rehearsable restore of an encrypted Sell backup into a DISPOSABLE PostgreSQL.
#
# This script never touches the production database. It decrypts a chosen
# encrypted dump, restores it into a throwaway PostgreSQL container, and prints
# a short verification summary (row counts, schema readiness). Run it monthly to
# prove the backups are actually restorable — an untested backup is not a backup.
#
# Usage:
#   APPSELL_BACKUP_ENCRYPTION_SECRET=... \
#     restore-native.sh /path/to/appsell_native_90-<stamp>.dump.gpg
#
# Required environment:
#   APPSELL_BACKUP_ENCRYPTION_SECRET  gpg symmetric passphrase used at backup time
#
# Optional environment:
#   RESTORE_PG_IMAGE   postgres image for the disposable target (BACKUP_PG_IMAGE)
#   BACKUP_PG_IMAGE    client image for restore/verify (lib-backup.sh default)
#   RESTORE_ROLES_SOURCE file whose `create role` statements define the roles
#                      created in the target (infra/postgres/init/001_roles.sh)
#   RESTORE_KEEP_ALIVE if "1", leave the disposable container running for
#                      inspection instead of tearing it down.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/easypanel-native/backup/lib-backup.sh
source "$script_dir/lib-backup.sh"

enc_file="${1:?usage: restore-native.sh <encrypted-dump.gpg>}"
[[ -s "$enc_file" ]] || die "encrypted dump not found or empty: $enc_file"
require_env APPSELL_BACKUP_ENCRYPTION_SECRET

readonly restore_pg_image="${RESTORE_PG_IMAGE:-$BACKUP_PG_IMAGE}"
readonly roles_source="${RESTORE_ROLES_SOURCE:-${script_dir}/../../../infra/postgres/init/001_roles.sh}"
readonly target_db="appsell_native_90"
readonly target_user="appsell_restore"
readonly run_id="appsell-restore-rehearsal-$$"
readonly network_name="${run_id}-net"
readonly target_container="${run_id}-postgres"
readonly target_alias="restore-target"

# The admin secret for the disposable target is generated fresh and lives only
# for the duration of this rehearsal. It is not a production credential.
admin_secret="$(openssl rand -hex 24)"

workdir="$(mktemp -d)"
plain_file="${workdir}/restore.dump"

cleanup() {
  rm -rf -- "$workdir" 2>/dev/null || true
  if [[ "${RESTORE_KEEP_ALIVE:-0}" == "1" ]]; then
    log "RESTORE_KEEP_ALIVE=1: leaving ${target_container} running (network ${network_name})"
    return
  fi
  docker rm -f "$target_container" >/dev/null 2>&1 || true
  docker network rm "$network_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

log "restore rehearsal start: ${enc_file##*/}"

log "step 1/6: decrypt"
decrypt_file "$enc_file" "$plain_file"

log "step 2/6: verify archive integrity before touching a database"
pg_verify_dump "$plain_file"

# The target must be at least the major the dump came from: an older server
# cannot load what a newer one dumped. The archive header says which it was.
dumped_from="$(docker run --rm -i "$BACKUP_PG_IMAGE" pg_restore --list <"$plain_file" \
  | awk '$2 == "Dumped" && $3 == "from" && !seen { print $6; seen = 1 }')"
dumped_from="${dumped_from%%.*}"
[[ -n "$dumped_from" && "$dumped_from" != *[!0-9]* ]] \
  || die "archive header does not say which server version it was dumped from"
target_major="$(pg_client_major "$restore_pg_image")"
(( target_major >= dumped_from )) \
  || die "RESTORE_PG_IMAGE is PostgreSQL ${target_major}, older than the dump's ${dumped_from}"

roles="$(pg_roles_from_source "$roles_source")"

log "step 3/6: launch disposable PostgreSQL (${restore_pg_image})"
docker network create "$network_name" >/dev/null
# POSTGRES_PASSWORD is forwarded by name only (no `=value`) so the value never
# reaches the world-readable argv; POSTGRES_USER/DB are not secrets.
POSTGRES_PASSWORD="$admin_secret" docker run -d --name "$target_container" \
  --network "$network_name" --network-alias "$target_alias" \
  -e POSTGRES_USER="$target_user" \
  -e POSTGRES_PASSWORD \
  -e POSTGRES_DB="$target_db" \
  "$restore_pg_image" >/dev/null

# Point the lib client wrapper at this rehearsal network.
export BACKUP_DOCKER_NETWORK="$network_name"

for _ in $(seq 1 30); do
  docker exec "$target_container" pg_isready -q -U "$target_user" && break
  sleep 2
done
docker exec "$target_container" pg_isready -q -U "$target_user" \
  || die "disposable PostgreSQL did not become ready"

log "step 4/6: create the Sell roles from ${roles_source##*/} (NOLOGIN, no password)"
mapfile -t role_list <<<"$roles"
pg_create_nologin_roles "$target_alias" 5432 "$target_user" "$target_db" \
  "$admin_secret" "${role_list[@]}"
log "roles created in the disposable target: ${role_list[*]}"

log "step 5/6: pg_restore into disposable target"
pg_restore_from_file "$target_alias" 5432 "$target_user" "$target_db" \
  "$admin_secret" "$plain_file" \
  || die "pg_restore failed; a 'role ... does not exist' above means the dump cites a role that ${roles_source##*/} does not create"

log "step 6/6: verification summary"
table_count="$(pg_scalar "$target_alias" 5432 "$target_user" "$target_db" "$admin_secret" \
  "select count(*) from information_schema.tables where table_schema not in ('pg_catalog','information_schema')")"
log "restored non-system tables: ${table_count}"
[[ "${table_count:-0}" -gt 0 ]] || die "restore produced no user tables — backup is unusable"

# identity_user is seeded by the bootstrap; if present, report its row count as a
# smoke signal that real data survived the round trip.
if user_rows="$(pg_scalar "$target_alias" 5432 "$target_user" "$target_db" "$admin_secret" \
  "select count(*) from identity_user" 2>/dev/null)"; then
  log "identity_user rows restored: ${user_rows}"
fi

log "restore rehearsal ok: ${enc_file##*/} is restorable"
