#!/usr/bin/env bash
# Shared helpers for the native PostgreSQL logical backup of the Autonom.ia Sell
# database (appsell_native_90 on the EasyPanel service postgres-native-90).
#
# Design constraints:
# - No hardcoded credentials. Every secret arrives via environment variables.
# - No secret is ever placed on a command line (argv). The host `docker`/`gpg`
#   process argv is world-readable through `/proc/<pid>/cmdline` and `ps`, so:
#     * the gpg passphrase is fed on a file descriptor (`--passphrase-fd 3`);
#     * container secrets (PGPASSWORD, S3 credentials) are forwarded to the
#       container by env-var NAME only (`docker run -e VAR`, no `=value`), with
#       the value exported solely for that one invocation. Docker then reads the
#       value from its own environment (`/proc/<pid>/environ`, mode 0400 —
#       readable only by the owner and root) instead of from argv.
#   Passing `-e VAR=value` would leak the value into the world-readable argv and
#   is therefore forbidden in this file.
# - PostgreSQL client tooling runs inside a pinned client container so the host
#   only needs Docker plus gpg; the server version is matched by the image tag.
# - Encryption is gpg symmetric AES256 (portable, present on CI and the host).
#
# This file is sourced by backup-native.sh, restore-native.sh and
# test-backup-native.sh. It defines functions only; it starts nothing.

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die() { log "FATAL: $*"; exit 1; }

# ---------------------------------------------------------------------------
# Environment validation
# ---------------------------------------------------------------------------
# require_env VAR_NAME [VAR_NAME ...] — fail fast if any is unset or empty.
require_env() {
  local missing=0 name
  for name in "$@"; do
    if [[ -z "${!name:-}" ]]; then
      log "missing required environment variable: $name"
      missing=1
    fi
  done
  [[ "$missing" -eq 0 ]] || die "one or more required environment variables are unset"
}

# ---------------------------------------------------------------------------
# PostgreSQL client container wrapper
# ---------------------------------------------------------------------------
# The client image and Docker network are taken from globals so callers stay
# terse. The connection secret is injected via PGPASSWORD in the container's
# environment (uppercase env name; never a lowercase conninfo string). It is
# forwarded by NAME only (`-e PGPASSWORD`, no `=value`) with the value exported
# just for that invocation, so it never appears on the world-readable argv.
: "${BACKUP_PG_IMAGE:=postgres:16-alpine}"

# pg_dump_to_file HOST PORT USER DB SECRET OUTFILE
# Streams a custom-format (compressed, restorable) dump to a host file via
# container stdout. Custom format is required for pg_restore --list integrity
# checks and selective restore.
pg_dump_to_file() {
  local host="$1" port="$2" user="$3" db="$4" secret="$5" outfile="$6"
  require_env BACKUP_DOCKER_NETWORK
  PGPASSWORD="$secret" docker run --rm --network "$BACKUP_DOCKER_NETWORK" \
    -e PGPASSWORD \
    "$BACKUP_PG_IMAGE" \
    pg_dump --format=custom --compress=6 --no-owner --no-privileges \
      --host="$host" --port="$port" --username="$user" --dbname="$db" \
    >"$outfile"
}

# pg_verify_dump DUMPFILE
# Proves the archive is a well-formed custom-format dump by listing its table of
# contents through a client container. Empty TOC or a non-zero exit is a failure.
pg_verify_dump() {
  local dumpfile="$1" toc
  toc="$(docker run --rm -i "$BACKUP_PG_IMAGE" pg_restore --list <"$dumpfile")" \
    || die "pg_restore --list failed on ${dumpfile##*/}: archive is corrupt or truncated"
  [[ -n "$toc" ]] || die "pg_restore --list produced an empty table of contents for ${dumpfile##*/}"
  local entries
  entries="$(grep -cvE '^;|^$' <<<"$toc" || true)"
  log "integrity ok: ${dumpfile##*/} lists ${entries} archive entries"
}

# pg_restore_from_file HOST PORT USER DB SECRET DUMPFILE
# Restores a custom-format dump into an existing (empty) database. --clean
# --if-exists makes the restore idempotent for rehearsals; --no-owner and
# --no-privileges drop role/ACL dependencies so a disposable target accepts it.
pg_restore_from_file() {
  local host="$1" port="$2" user="$3" db="$4" secret="$5" dumpfile="$6"
  require_env BACKUP_DOCKER_NETWORK
  PGPASSWORD="$secret" docker run --rm -i --network "$BACKUP_DOCKER_NETWORK" \
    -e PGPASSWORD \
    "$BACKUP_PG_IMAGE" \
    pg_restore --clean --if-exists --no-owner --no-privileges \
      --exit-on-error \
      --host="$host" --port="$port" --username="$user" --dbname="$db" \
    <"$dumpfile"
}

# pg_scalar HOST PORT USER DB SECRET SQL
# Runs a single-value query and echoes the result (used for comparisons/asserts).
pg_scalar() {
  local host="$1" port="$2" user="$3" db="$4" secret="$5" sql="$6"
  require_env BACKUP_DOCKER_NETWORK
  PGPASSWORD="$secret" docker run --rm --network "$BACKUP_DOCKER_NETWORK" \
    -e PGPASSWORD \
    "$BACKUP_PG_IMAGE" \
    psql -XAt --set=ON_ERROR_STOP=1 \
      --host="$host" --port="$port" --username="$user" --dbname="$db" \
      --command="$sql"
}

# ---------------------------------------------------------------------------
# Encryption (gpg symmetric AES256)
# ---------------------------------------------------------------------------
# The passphrase is read from APPSELL_BACKUP_ENCRYPTION_SECRET and handed to gpg
# on file descriptor 3, so it never appears on the command line or on disk.

encrypt_file() {
  local plainfile="$1" encfile="$2"
  require_env APPSELL_BACKUP_ENCRYPTION_SECRET
  command -v gpg >/dev/null 2>&1 || die "gpg is required for encryption but was not found"
  gpg --batch --yes --quiet --no-symkey-cache --pinentry-mode loopback --passphrase-fd 3 \
    --symmetric --cipher-algo AES256 --compress-algo none \
    --output "$encfile" "$plainfile" \
    3<<<"$APPSELL_BACKUP_ENCRYPTION_SECRET" \
    || die "gpg encryption failed for ${plainfile##*/}"
  [[ -s "$encfile" ]] || die "encryption produced an empty artifact: ${encfile##*/}"
  log "encrypted ${plainfile##*/} -> ${encfile##*/} ($(wc -c <"$encfile") bytes)"
}

decrypt_file() {
  local encfile="$1" plainfile="$2"
  require_env APPSELL_BACKUP_ENCRYPTION_SECRET
  command -v gpg >/dev/null 2>&1 || die "gpg is required for decryption but was not found"
  gpg --batch --yes --quiet --no-symkey-cache --pinentry-mode loopback --passphrase-fd 3 \
    --decrypt --output "$plainfile" "$encfile" \
    3<<<"$APPSELL_BACKUP_ENCRYPTION_SECRET" \
    || die "gpg decryption failed for ${encfile##*/} (wrong passphrase or corrupt file)"
  [[ -s "$plainfile" ]] || die "decryption produced an empty artifact: ${plainfile##*/}"
  log "decrypted ${encfile##*/} -> ${plainfile##*/}"
}

# ---------------------------------------------------------------------------
# Local rotation
# ---------------------------------------------------------------------------
# rotate_local DIR GLOB KEEP — keep the KEEP newest files matching DIR/GLOB,
# delete the rest. Newest is decided by filename (timestamps sort lexically).
# NOTE: the retention count is held in `keep_count`, not `keep`, because callers
# (backup-native.sh) expose the retention limit as a `readonly keep` global and a
# `local keep` here would collide with it ("local: keep: readonly variable").
rotate_local() {
  local dir="$1" glob="$2" keep_count="$3"
  local -a files=()
  while IFS= read -r f; do files+=("$f"); done \
    < <(find "$dir" -maxdepth 1 -type f -name "$glob" | sort -r)
  local total="${#files[@]}"
  if (( total <= keep_count )); then
    log "rotation: ${total} local backups, keeping all (limit ${keep_count})"
    return 0
  fi
  local i
  for (( i = keep_count; i < total; i++ )); do
    rm -f -- "${files[$i]}"
    log "rotation: pruned ${files[$i]##*/}"
  done
  log "rotation: kept ${keep_count} newest of ${total} local backups"
}

# ---------------------------------------------------------------------------
# Off-host shipping (pluggable destination)
# ---------------------------------------------------------------------------
# ship_offhost ENCFILE — dispatches on APPSELL_BACKUP_DEST_KIND:
#   none      : local retention only (default)
#   local-dir : copy to APPSELL_BACKUP_LOCAL_DIR (used by the CI rehearsal)
#   s3        : upload to a MinIO/S3 bucket via the MinIO client (mc) container
#   rsync     : rsync over SSH to APPSELL_BACKUP_RSYNC_TARGET
# Only the encrypted artifact is ever shipped off-host.
ship_offhost() {
  local encfile="$1"
  local kind="${APPSELL_BACKUP_DEST_KIND:-none}"
  case "$kind" in
    none)
      log "off-host: APPSELL_BACKUP_DEST_KIND=none, keeping local copy only"
      ;;
    local-dir)
      require_env APPSELL_BACKUP_LOCAL_DIR
      mkdir -p "$APPSELL_BACKUP_LOCAL_DIR"
      cp -- "$encfile" "$APPSELL_BACKUP_LOCAL_DIR/${encfile##*/}"
      log "off-host: copied ${encfile##*/} to ${APPSELL_BACKUP_LOCAL_DIR}"
      ;;
    s3)
      ship_offhost_s3 "$encfile"
      ;;
    rsync)
      ship_offhost_rsync "$encfile"
      ;;
    *)
      die "unknown APPSELL_BACKUP_DEST_KIND: $kind (expected none|local-dir|s3|rsync)"
      ;;
  esac
}

# S3-compatible (MinIO) upload via the mc container. Credentials arrive via env
# and are wired into a throwaway alias inside the container; nothing is written
# to a config file on the host. The MC_HOST_offhost URL embeds the access/secret
# keys, so it is forwarded to the container by NAME only (`-e MC_HOST_offhost`,
# no `=value`) — the value is exported just for this invocation and never lands
# on the world-readable argv.
ship_offhost_s3() {
  local encfile="$1"
  require_env APPSELL_BACKUP_S3_ENDPOINT APPSELL_BACKUP_S3_BUCKET \
    APPSELL_BACKUP_S3_ACCESS_KEY APPSELL_BACKUP_S3_SECRET_KEY
  local prefix="${APPSELL_BACKUP_S3_PREFIX:-sell/native-90}"
  local image="${BACKUP_MC_IMAGE:-minio/mc:latest}"
  local dir="${encfile%/*}" base="${encfile##*/}"
  local mc_host="https://${APPSELL_BACKUP_S3_ACCESS_KEY}:${APPSELL_BACKUP_S3_SECRET_KEY}@${APPSELL_BACKUP_S3_ENDPOINT#https://}"
  MC_HOST_offhost="$mc_host" docker run --rm \
    -e MC_HOST_offhost \
    -v "$dir:/data:ro" \
    "$image" \
    cp "/data/${base}" "offhost/${APPSELL_BACKUP_S3_BUCKET}/${prefix}/${base}" \
    || die "off-host S3 upload failed for ${base}"
  log "off-host: uploaded ${base} to s3://${APPSELL_BACKUP_S3_BUCKET}/${prefix}/"
}

# rsync-over-SSH upload. The SSH identity path and known_hosts arrive via env so
# no key material is embedded here.
ship_offhost_rsync() {
  local encfile="$1"
  require_env APPSELL_BACKUP_RSYNC_TARGET APPSELL_BACKUP_SSH_KEY_FILE
  command -v rsync >/dev/null 2>&1 || die "rsync is required for the rsync destination"
  local known_hosts="${APPSELL_BACKUP_SSH_KNOWN_HOSTS:-$HOME/.ssh/known_hosts}"
  rsync --archive --partial \
    -e "ssh -i ${APPSELL_BACKUP_SSH_KEY_FILE} -o UserKnownHostsFile=${known_hosts} -o StrictHostKeyChecking=yes" \
    "$encfile" "$APPSELL_BACKUP_RSYNC_TARGET" \
    || die "off-host rsync upload failed for ${encfile##*/}"
  log "off-host: rsynced ${encfile##*/} to ${APPSELL_BACKUP_RSYNC_TARGET}"
}
