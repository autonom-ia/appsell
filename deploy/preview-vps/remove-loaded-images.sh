#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 RUN_ROOT" >&2
  exit 2
fi
run_root="$(realpath -e "$1")"
[[ "$run_root" =~ ^/opt/autonomia-sell-preview/issue-66/[0-9a-f]{12}$ ]] || exit 2
config_dir="$run_root/config"
# shellcheck source=docker-local-guard.sh
source "$config_dir/docker-local-guard.sh"
pin_local_preview_docker
record_file="$run_root/loaded-images.tsv"
[[ -e "$record_file" ]] || exit 0
[[ -f "$record_file" && ! -L "$record_file" ]] || exit 2
[[ "$(stat -c '%u:%g' "$record_file")" == "0:0" ]] || exit 2
[[ "$(stat -c '%a' "$record_file")" == "600" && "$(stat -c '%h' "$record_file")" == "1" ]] || exit 2

while IFS='|' read -r image image_id source_sha; do
  [[ -n "$image" ]] || continue
  [[ "$source_sha" =~ ^[0-9a-f]{40}$ ]] || exit 2
  [[ -z "$image_id" || "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 2
  case "$image" in
    "autonomia-sell-preview-sell:$source_sha"|\
    "autonomia-sell-preview-domain:$source_sha"|\
    "autonomia-sell-preview-base-alpine:$source_sha"|\
    "autonomia-sell-preview-base-postgres:$source_sha"|\
    "autonomia-sell-preview-base-redis:$source_sha"|\
    "autonomia-identity-auth-preview:$source_sha"|\
    "autonomia-identity-foundation-preview:$source_sha")
      ;;
    *)
      echo "Unexpected loaded image record" >&2
      exit 2
      ;;
  esac
  if docker image inspect "$image" >/dev/null 2>&1; then
    if [[ -n "$image_id" ]]; then
      [[ "$(docker image inspect --format '{{.Id}}' "$image")" == "$image_id" ]] || exit 2
    fi
    [[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" == "$source_sha" ]] || exit 2
    docker image rm "$image" >/dev/null
  fi
done <"$record_file"
rm -f -- "$record_file"
echo "Removed only image tags loaded by this Issue #66 run."
