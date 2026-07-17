#!/usr/bin/env bash
set -euo pipefail

[[ "$(id -u)" == "0" ]] || { echo "evidence migration fixture must run as root" >&2; exit 2; }
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_sha=dddddddddddddddddddddddddddddddddddddddd
legacy_sha=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
run_root="/opt/autonomia-sell-preview/issue-77/${source_sha:0:12}"
volume=sell-autonomia_runtime-preview_runtime_evidence
mountpoint="/var/lib/docker/volumes/$volume/_data"
fixture="$(mktemp -d)"
env_file="$fixture/runtime-preview.env"
shim="$fixture/bin"
state="$fixture/state"
rm_failed_once="$fixture/rm-failed-once"
[[ ! -e "$run_root" && ! -L "$run_root" && ! -e "/var/lib/docker/volumes/$volume" ]] || {
  echo "evidence migration fixture collision" >&2
  exit 2
}
cleanup() {
  rm -rf --one-file-system -- "$run_root" "$fixture" "/var/lib/docker/volumes/$volume"
}
trap cleanup EXIT
install -d -o root -g root -m 0700 "$run_root" "$shim" "$mountpoint"
printf 'APPSELL_SOURCE_SHA=%s\n' "$source_sha" >"$env_file"
chown root:root "$env_file"
chmod 0600 "$env_file"
install -o root -g root -m 0600 /dev/null "$run_root/protected-services.before.tsv"
printf 'legacy evidence\n' >"$mountpoint/evidence.txt"
printf 'legacy\n' >"$state"

cat >"$shim/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
volume=sell-autonomia_runtime-preview_runtime_evidence
mode="$(<"$MOCK_VOLUME_STATE")"
case "$1 $2" in
  "service ls") exit 0 ;;
  "volume ls") [[ "$mode" == "absent" ]] || printf '%s\n' "$volume" ;;
  "volume inspect")
    [[ "$mode" != "absent" ]] || exit 1
    if [[ "$mode" == "legacy" ]]; then
      jq -cn --arg name "$volume" --arg mount "$MOCK_VOLUME_MOUNTPOINT" \
        '[{Name:$name,Driver:"local",Scope:"local",Options:null,Mountpoint:$mount,Labels:{
          "com.docker.compose.config-hash":"legacy-hash",
          "com.docker.compose.project":"sell-autonomia_runtime-preview",
          "com.docker.compose.version":"5.1.3",
          "com.docker.compose.volume":"runtime_evidence",
          "com.autonomia.project":"sell-autonomia","com.autonomia.issue":"77",
          "com.autonomia.environment":"local-ci-easypanel-preview",
          "com.autonomia.source-sha":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"}}]'
    else
      jq -cn --arg name "$volume" --arg mount "$MOCK_VOLUME_MOUNTPOINT" \
        '[{Name:$name,Driver:"local",Scope:"local",Options:null,Mountpoint:$mount,Labels:{
          "com.docker.compose.project":"sell-autonomia_runtime-preview",
          "com.docker.compose.volume":"runtime_evidence",
          "com.autonomia.project":"sell-autonomia","com.autonomia.issue":"77",
          "com.autonomia.environment":"local-ci-easypanel-preview",
          "com.autonomia.purpose":"runtime-e2e-evidence"}}]'
    fi
    ;;
  "ps -aq") exit 0 ;;
  "volume rm")
    printf 'absent\n' >"$MOCK_VOLUME_STATE"
    if [[ ! -e "$MOCK_RM_FAILED_ONCE" ]]; then touch "$MOCK_RM_FAILED_ONCE"; exit 1; fi
    ;;
  "volume create")
    printf 'stable\n' >"$MOCK_VOLUME_STATE"
    printf '%s\n' "$volume"
    ;;
  *) echo "unexpected mock docker command: $*" >&2; exit 2 ;;
esac
SH
chmod 0755 "$shim/docker"
export MOCK_VOLUME_STATE="$state"
export MOCK_VOLUME_MOUNTPOINT="$mountpoint"
export MOCK_RM_FAILED_ONCE="$rm_failed_once"

env PATH="$shim:$PATH" CONFIRM_RUNTIME_PREVIEW_EVIDENCE_MIGRATION=sell-autonomia-evidence-v1 \
  "$here/migrate-legacy-runtime-evidence.sh" "$run_root" "$env_file" "$source_sha" "$legacy_sha"
[[ "$(<"$state")" == "stable" ]]
archive="$run_root/legacy-runtime-evidence-${legacy_sha:0:12}.tar.gz"
attestation="$run_root/legacy-runtime-evidence-${legacy_sha:0:12}.json"
[[ -s "$archive" && -s "$attestation" ]]

env PATH="$shim:$PATH" CONFIRM_RUNTIME_PREVIEW_EVIDENCE_MIGRATION=sell-autonomia-evidence-v1 \
  "$here/migrate-legacy-runtime-evidence.sh" "$run_root" "$env_file" "$source_sha" "$legacy_sha"
[[ "$(<"$state")" == "stable" ]]
echo "EasyPanel legacy evidence migration resume: passed"
