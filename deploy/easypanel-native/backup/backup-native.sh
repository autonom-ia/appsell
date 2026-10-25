#!/usr/bin/env bash
# Daily logical backup of the Autonom.ia Sell native PostgreSQL database.
#
# Pipeline: pg_dump (custom format) -> integrity check (pg_restore --list) ->
# gpg AES256 encryption -> off-host shipping -> local rotation (keep 7 daily).
#
# The dump connects to the native PostgreSQL service (default network alias
# sell-autonomia_postgres-native-90) through a pinned client container, so the
# host needs only Docker plus gpg. Nothing is written to the source database.
#
# Required environment (no defaults — must be provided at run time):
#   BACKUP_DOCKER_NETWORK            EasyPanel project Docker network to join
#   APPSELL_POSTGRES_HOST            PostgreSQL host / network alias
#   APPSELL_POSTGRES_USER            role used for the dump (read is enough)
#   APPSELL_POSTGRES_SECRET          that role's password (value only via env)
#   APPSELL_BACKUP_ENCRYPTION_SECRET gpg symmetric passphrase
#
# Optional environment (documented defaults):
#   APPSELL_POSTGRES_PORT            5432
#   APPSELL_POSTGRES_DB              appsell_native_90
#   APPSELL_BACKUP_DIR               /var/backups/appsell-native-90
#   APPSELL_BACKUP_KEEP              7
#   APPSELL_BACKUP_DEST_KIND         none | local-dir | s3 | rsync   (default none)
#   BACKUP_PG_IMAGE                  the production server image (lib-backup.sh)
#   (destination-specific vars are validated by lib-backup.sh when selected)
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/easypanel-native/backup/lib-backup.sh
source "$script_dir/lib-backup.sh"

readonly pg_host="${APPSELL_POSTGRES_HOST:?APPSELL_POSTGRES_HOST is required}"
readonly pg_port="${APPSELL_POSTGRES_PORT:-5432}"
readonly pg_user="${APPSELL_POSTGRES_USER:?APPSELL_POSTGRES_USER is required}"
readonly pg_db="${APPSELL_POSTGRES_DB:-appsell_native_90}"
readonly backup_dir="${APPSELL_BACKUP_DIR:-/var/backups/appsell-native-90}"
readonly keep="${APPSELL_BACKUP_KEEP:-7}"

require_env BACKUP_DOCKER_NETWORK APPSELL_POSTGRES_SECRET APPSELL_BACKUP_ENCRYPTION_SECRET

readonly stamp="$(date -u +%Y%m%dT%H%M%SZ)"
readonly base="appsell_native_90-${stamp}"
readonly dump_file="${backup_dir}/${base}.dump"
readonly enc_file="${dump_file}.gpg"

cleanup() {
  # The plaintext dump never leaves the host encrypted-at-rest boundary: it is
  # removed as soon as the encrypted artifact exists (or on any failure).
  [[ -n "${dump_file:-}" && -f "$dump_file" ]] && rm -f -- "$dump_file"
}
trap cleanup EXIT

mkdir -p "$backup_dir"
chmod 0700 "$backup_dir" 2>/dev/null || true

log "backup start: db=${pg_db} host=${pg_host}:${pg_port} dest=${APPSELL_BACKUP_DEST_KIND:-none}"

pg_assert_client_not_older "$pg_host" "$pg_port" "$pg_user" "$pg_db" "$APPSELL_POSTGRES_SECRET"

log "step 1/5: pg_dump (custom format) -> ${dump_file##*/}"
pg_dump_to_file "$pg_host" "$pg_port" "$pg_user" "$pg_db" \
  "$APPSELL_POSTGRES_SECRET" "$dump_file"
[[ -s "$dump_file" ]] || die "pg_dump produced an empty dump"

log "step 2/5: integrity check (pg_restore --list)"
pg_verify_dump "$dump_file"

log "step 3/5: encrypt (gpg AES256) -> ${enc_file##*/}"
encrypt_file "$dump_file" "$enc_file"
chmod 0600 "$enc_file" 2>/dev/null || true

log "step 4/5: ship off-host"
ship_offhost "$enc_file"

log "step 5/5: rotate local (keep ${keep})"
rotate_local "$backup_dir" 'appsell_native_90-*.dump.gpg' "$keep"

log "backup ok: ${enc_file##*/}"
