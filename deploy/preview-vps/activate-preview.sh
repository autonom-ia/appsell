#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "Usage: $0 RUN_ROOT APPSELL_SHA AUTH_SHA FOUNDATION_SHA" >&2
  exit 2
fi
run_root="$(realpath -e "$1")"
appsell_sha="$2"
auth_sha="$3"
foundation_sha="$4"
[[ "$run_root" =~ ^/opt/autonomia-sell-preview/issue-66/[0-9a-f]{12}$ ]] || exit 2
for sha in "$appsell_sha" "$auth_sha" "$foundation_sha"; do
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 2
done
[[ "${appsell_sha:0:12}" == "$(basename "$run_root")" ]] || exit 2
command -v flock >/dev/null 2>&1 || { echo "Missing required command: flock" >&2; exit 2; }
lock_dir=/run/lock/autonomia-sell-preview
[[ ! -L "$lock_dir" ]] || { echo "Refusing a symlink preview lock directory" >&2; exit 2; }
if [[ ! -e "$lock_dir" ]]; then
  install -d -o root -g root -m 0700 "$lock_dir"
fi
[[ -d "$lock_dir" && ! -L "$lock_dir" ]] || { echo "Invalid preview lock directory" >&2; exit 2; }
[[ "$(stat -c '%u:%g:%a' "$lock_dir")" == "0:0:700" ]] || {
  echo "Preview lock directory must be root:root 0700" >&2
  exit 2
}
lock_file="$lock_dir/issue-66.lock"
[[ ! -L "$lock_file" ]] || { echo "Refusing a symlink preview lock file" >&2; exit 2; }
if [[ ! -e "$lock_file" ]]; then
  (
    umask 077
    set -o noclobber
    : >"$lock_file"
  )
fi
[[ -f "$lock_file" && ! -L "$lock_file" ]] || { echo "Invalid preview lock file" >&2; exit 2; }
[[ "$(stat -c '%u:%g:%a:%h' "$lock_file")" == "0:0:600:1" ]] || {
  echo "Preview lock file must be root:root 0600 with one link" >&2
  exit 2
}
exec 9<>"$lock_file"
flock -n 9 || { echo "Another Issue #66 activation is already running" >&2; exit 2; }

config_dir="$run_root/config"
artifacts_dir="$run_root/artifacts"
env_file="$run_root/preview.env"
before="$run_root/snapshots/before"
after="$run_root/snapshots/after"
[[ -f "$config_dir/validate-root-tree.sh" && ! -L "$config_dir/validate-root-tree.sh" ]] || exit 2
[[ -d "$config_dir" && ! -L "$config_dir" && -d "$artifacts_dir" && ! -L "$artifacts_dir" ]] || {
  echo "Config and artifact roots must be non-symlink directories" >&2
  exit 2
}
bash "$config_dir/validate-root-tree.sh" "$config_dir"
bash "$config_dir/validate-root-tree.sh" "$artifacts_dir"
[[ "$(stat -c '%u:%g:%a' "$run_root")" == "0:0:700" ]] || {
  echo "Run root must be root:root 0700" >&2
  exit 2
}
root_entries="$(find "$run_root" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort)"
[[ "$root_entries" == $'artifacts\nconfig' ]] || {
  echo "Run root must initially contain only config/ and artifacts/" >&2
  exit 2
}
# shellcheck source=docker-local-guard.sh
source "$config_dir/docker-local-guard.sh"
pin_local_preview_docker
[[ ! -e "$env_file" && ! -L "$env_file" && ! -e "$before" && ! -L "$before" && ! -e "$after" && ! -L "$after" ]] || {
  echo "Refusing to reuse a previously activated run root" >&2
  exit 2
}

rollback() {
  local status=$?
  trap - ERR INT TERM
  (( status != 0 )) || status=1
  echo "Activation failed; rolling back the exact Issue #66 preview resources." >&2
  set +e
  if [[ -f "$env_file" && ! -L "$env_file" ]]; then
    timeout --signal=TERM --kill-after=30s 10m "$config_dir/teardown.sh" "$config_dir/compose.preview.yaml" "$env_file"
  else
    if timeout --signal=TERM --kill-after=30s 5m "$config_dir/remove-loaded-images.sh" "$run_root"; then
      if findmnt -rn -o TARGET | awk -v root="$run_root" '$0 == root || index($0, root "/") == 1 { found=1 } END { exit !found }'; then
        echo "Refusing to remove run root containing a mount target" >&2
      else
        rm -rf --one-file-system -- "$run_root"
      fi
    else
      echo "Image cleanup failed; preserving the run root and its ownership record" >&2
    fi
  fi
  exit "$status"
}
trap rollback ERR INT TERM

timeout --signal=TERM --kill-after=30s 5m "$config_dir/snapshot-existing.sh" "$before"
AUTONOMIA_PREVIEW_ORCHESTRATED=1 timeout --signal=TERM --kill-after=30s 30m "$config_dir/load-images.sh" \
  "$run_root" "$appsell_sha" "$auth_sha" "$foundation_sha"
timeout --signal=TERM --kill-after=30s 2m "$config_dir/generate-preview-env.sh" \
  "$env_file" "$appsell_sha" "$auth_sha" "$foundation_sha"
AUTONOMIA_PREVIEW_ORCHESTRATED=1 timeout --signal=TERM --kill-after=30s 15m \
  "$config_dir/up.sh" "$config_dir/compose.preview.yaml" "$env_file"
timeout --signal=TERM --kill-after=30s 45m "$config_dir/verify.sh" \
  "$config_dir/compose.preview.yaml" "$env_file"
timeout --signal=TERM --kill-after=30s 5m "$config_dir/snapshot-existing.sh" "$after"
timeout --signal=TERM --kill-after=30s 5m "$config_dir/compare-existing.sh" "$before" "$after"

trap - ERR INT TERM
echo "Issue #66 preview is active only after preflight, E2E and existing-state comparison passed."
