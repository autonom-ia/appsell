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
readonly s3_container="appsell-backup-test-s3"
# Same image the CI storage stack runs (compose.yaml).
readonly s3_image="chrislusf/seaweedfs:4.39@sha256:c7d6c721b30ae711db766bbbfd40192776e263d4e51e22f57baef7bef93c12c6"
readonly s3_bucket="appsell-backup-test"
# Same major as production by default (lib-backup.sh), so the major checks
# below exercise the real pair.
readonly postgres_image="${TEST_POSTGRES_IMAGE:-$BACKUP_PG_IMAGE}"
readonly roles_source="${script_dir}/../../../infra/postgres/init/001_roles.sh"
readonly test_db="appsell_native_90"
readonly test_user="appsell_backup_test"

# All secrets below are ephemeral, generated per run, and never leave the runner.
source_secret="$(openssl rand -hex 24)"
target_secret="$(openssl rand -hex 24)"
export APPSELL_BACKUP_ENCRYPTION_SECRET="$(openssl rand -hex 32)"
s3_admin_key="$(openssl rand -hex 10)"
s3_admin_secret="$(openssl rand -hex 20)"
s3_writer_key="$(openssl rand -hex 10)"
s3_writer_secret="$(openssl rand -hex 20)"

export BACKUP_DOCKER_NETWORK="$network_name"
export BACKUP_PG_IMAGE="$postgres_image"

cleanup() {
  docker rm -f "$source_container" "$target_container" "$s3_container" >/dev/null 2>&1 || true
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
  # POSTGRES_PASSWORD by name only (no `=value`) — keep secrets off the argv.
  POSTGRES_PASSWORD="$secret" docker run -d --name "$container" \
    --network "$network_name" --network-alias "$alias" \
    -e POSTGRES_USER="$test_user" \
    -e POSTGRES_PASSWORD \
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

echo "--- step 0: the role source and the major guard"
roles="$(pg_roles_from_source "$roles_source")"
mapfile -t role_list <<<"$roles"
for needed in appsell_owner appsell_runtime; do
  printf '%s\n' "${role_list[@]}" | grep -qxF "$needed" \
    || { echo "${roles_source##*/} does not yield ${needed}" >&2; exit 1; }
done
pg_assert_client_not_older "$source_alias" 5432 "$test_user" "$test_db" "$source_secret"
# A client older than the server must be refused before any dump (#492).
if (BACKUP_PG_IMAGE=postgres:16-alpine pg_assert_client_not_older \
    "$source_alias" 5432 "$test_user" "$test_db" "$source_secret") 2>/dev/null; then
  echo "a PostgreSQL 16 client against a newer server must be refused" >&2
  exit 1
fi

echo "--- step 1: seed representative data into the source database"
# The Sell schema names roles in its RLS policies. pg_dump does not carry
# roles, so the restore has to create them; these policies prove it does.
pg_create_nologin_roles "$source_alias" 5432 "$test_user" "$test_db" \
  "$source_secret" "${role_list[@]}"
PGPASSWORD="$source_secret" docker exec -e PGPASSWORD "$source_container" \
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
    create table asset_blob (id bigint primary key, payload text not null);
    alter table asset_blob enable row level security;
    create policy asset_blob_owner_all on asset_blob to appsell_owner
      using (true) with check (true);
    create policy asset_blob_runtime_read on asset_blob for select to appsell_runtime
      using (true);
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

echo "--- step 5b: ship off-host (s3) with an identity that may only write"
# The production IAM grants s3:PutObject on the prefix and nothing else. The
# writer identity here has SeaweedFS "Write" on the bucket and no Read or
# List, so the upload passes only if the client needs nothing but PutObject.
s3_config="$(printf '{"identities":[
  {"name":"admin","credentials":[{"accessKey":"%s","secretKey":"%s"}],
   "actions":["Admin","Read","List","Tagging","Write"]},
  {"name":"writer","credentials":[{"accessKey":"%s","secretKey":"%s"}],
   "actions":["Write:%s"]}]}' \
  "$s3_admin_key" "$s3_admin_secret" "$s3_writer_key" "$s3_writer_secret" "$s3_bucket")"
# The config carries keys, so it goes in by env NAME and is written inside
# the container; nothing lands on the argv or in a host file.
SEAWEED_S3_CONFIG="$s3_config" docker run -d --name "$s3_container" \
  --network "$network_name" \
  -e SEAWEED_S3_CONFIG --entrypoint /bin/sh "$s3_image" -ec '
    mkdir -p /tmp/weed
    printf %s "$SEAWEED_S3_CONFIG" > /tmp/weed/s3.json
    exec weed server -dir=/tmp/weed -s3 -s3.port=8333 -s3.config=/tmp/weed/s3.json
  ' >/dev/null
# By IP: the AWS CLI then addresses the bucket in the path. With a host name
# it may try <bucket>.<host>, which nothing here resolves.
s3_ip="$(docker inspect -f \
  "{{(index .NetworkSettings.Networks \"${network_name}\").IPAddress}}" "$s3_container")"
[[ -n "$s3_ip" ]] || { echo "the test S3 store has no address" >&2; exit 1; }
s3_endpoint="http://${s3_ip}:8333"

s3_download_dir="${workdir}/s3-download"
mkdir -p "$s3_download_dir"
# s3_as KEY SECRET ARGS... — one AWS CLI call against the test store.
s3_as() {
  local access="$1" secret="$2"
  shift 2
  AWS_ACCESS_KEY_ID="$access" AWS_SECRET_ACCESS_KEY="$secret" \
    AWS_DEFAULT_REGION=us-east-1 AWS_EC2_METADATA_DISABLED=true \
    AWS_REQUEST_CHECKSUM_CALCULATION=when_required \
    AWS_RESPONSE_CHECKSUM_VALIDATION=when_required \
    docker run --rm --network "$network_name" -v "$s3_download_dir:/out" \
    -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION \
    -e AWS_EC2_METADATA_DISABLED \
    -e AWS_REQUEST_CHECKSUM_CALCULATION -e AWS_RESPONSE_CHECKSUM_VALIDATION \
    "$BACKUP_S3_IMAGE" --endpoint-url "$s3_endpoint" "$@"
}
# Creating the bucket doubles as the readiness wait.
bucket_ready=0
for _ in $(seq 1 60); do
  if s3_as "$s3_admin_key" "$s3_admin_secret" s3api create-bucket \
      --bucket "$s3_bucket" >/dev/null 2>&1; then
    bucket_ready=1
    break
  fi
  sleep 2
done
if [[ "$bucket_ready" != "1" ]]; then
  docker logs "$s3_container" 2>&1 | tail -40 >&2
  echo "the test S3 store never accepted the bucket" >&2
  exit 1
fi

# The writer must really be write-only, or the upload below proves nothing.
if s3_as "$s3_writer_key" "$s3_writer_secret" s3api list-objects-v2 \
    --bucket "$s3_bucket" >/dev/null 2>&1; then
  echo "the writer identity can list the bucket; the test would prove nothing" >&2
  exit 1
fi

export APPSELL_BACKUP_DEST_KIND="s3"
export APPSELL_BACKUP_S3_ENDPOINT="$s3_endpoint"
export APPSELL_BACKUP_S3_BUCKET="$s3_bucket"
export APPSELL_BACKUP_S3_REGION="us-east-1"
export APPSELL_BACKUP_S3_ACCESS_KEY="$s3_writer_key"
export APPSELL_BACKUP_S3_SECRET_KEY="$s3_writer_secret"
export BACKUP_S3_DOCKER_NETWORK="$network_name"
s3_key="sell/native-90/${enc_file##*/}"

# A Content-MD5 that does not match the body must be refused, and nothing
# may land. MD5 of the empty string, which no encrypted dump has.
if (s3_content_md5() { printf '1B2M2Y8AsgTpgAmY7PhCfg=='; }; ship_offhost "$enc_file") \
    2>/dev/null; then
  echo "an upload with a wrong Content-MD5 must be refused but succeeded" >&2
  exit 1
fi
if s3_as "$s3_admin_key" "$s3_admin_secret" s3api head-object \
    --bucket "$s3_bucket" --key "$s3_key" >/dev/null 2>&1; then
  echo "a refused upload left an object behind" >&2
  exit 1
fi

ship_offhost "$enc_file"

# The writer cannot read back what it wrote.
if s3_as "$s3_writer_key" "$s3_writer_secret" s3api get-object \
    --bucket "$s3_bucket" --key "$s3_key" /out/writer-read.gpg >/dev/null 2>&1; then
  echo "the writer identity can read objects; the test would prove nothing" >&2
  exit 1
fi
s3_as "$s3_admin_key" "$s3_admin_secret" s3api get-object \
  --bucket "$s3_bucket" --key "$s3_key" /out/from-s3.gpg >/dev/null
cmp -s "$enc_file" "${s3_download_dir}/from-s3.gpg" \
  || { echo "the object in S3 differs from the local artifact" >&2; exit 1; }
decrypt_file "${s3_download_dir}/from-s3.gpg" "${workdir}/from-s3.dump"
pg_verify_dump "${workdir}/from-s3.dump"
unset APPSELL_BACKUP_S3_ACCESS_KEY APPSELL_BACKUP_S3_SECRET_KEY

echo "--- step 6: decrypt the OFF-HOST copy (proves the shipped file is usable)"
decrypt_file "$shipped" "$restored_plain"

echo "--- step 7: restore into the SECOND database"
pg_create_nologin_roles "$target_alias" 5432 "$test_user" "$test_db" \
  "$target_secret" "${role_list[@]}"
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

echo "--- step 10: the roles in a restore target cannot log in and have no password"
loginable="$(pg_scalar "$target_alias" 5432 "$test_user" "$test_db" "$target_secret" \
  "select count(*) from pg_authid where rolname = any (string_to_array('${role_list[*]}', ' '))
     and (rolcanlogin or rolpassword is not null)")"
[[ "$loginable" == "0" ]] || { echo "restore roles must be NOLOGIN without password" >&2; exit 1; }

echo "--- step 11: restore-native.sh restores a dump whose policies name roles"
restore_log="${workdir}/restore-native.log"
if ! bash "$script_dir/restore-native.sh" "$shipped" >"$restore_log" 2>&1; then
  cat "$restore_log" >&2
  echo "restore-native.sh failed on a dump with RLS policies" >&2
  exit 1
fi
grep -qF "restore rehearsal ok" "$restore_log" \
  || { cat "$restore_log" >&2; echo "restore-native.sh did not report success" >&2; exit 1; }

echo "--- step 12: a dump naming a role outside ${roles_source##*/} must fail the restore"
PGPASSWORD="$source_secret" docker exec -e PGPASSWORD "$source_container" \
  psql -XAt --set=ON_ERROR_STOP=1 -U "$test_user" -d "$test_db" -c "
    create role appsell_fora_da_fonte nologin;
    create policy asset_blob_fora on asset_blob to appsell_fora_da_fonte using (true);
  " >/dev/null
foreign_dump="${workdir}/foreign-role.dump"
pg_dump_to_file "$source_alias" 5432 "$test_user" "$test_db" "$source_secret" "$foreign_dump"
encrypt_file "$foreign_dump" "${foreign_dump}.gpg"
foreign_log="${workdir}/restore-foreign.log"
if bash "$script_dir/restore-native.sh" "${foreign_dump}.gpg" >"$foreign_log" 2>&1; then
  cat "$foreign_log" >&2
  echo "a dump naming a role the source does not create must fail the restore" >&2
  exit 1
fi
grep -qF 'role "appsell_fora_da_fonte" does not exist' "$foreign_log" \
  || { cat "$foreign_log" >&2; echo "restore failed for another reason" >&2; exit 1; }

echo "backup/restore rehearsal passed"
