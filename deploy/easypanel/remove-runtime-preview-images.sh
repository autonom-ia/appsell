#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 2 ]] || { echo "Usage: $0 RUN_ROOT APPSELL_SOURCE_SHA" >&2; exit 2; }
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-preview-common.sh
source "$here/runtime-preview-common.sh"
runtime_preview_require_root
runtime_preview_require_commands docker stat realpath
runtime_preview_assert_run_root "$1" "$2"
source_sha="$2"
record_file="$RUNTIME_PREVIEW_RUN_ROOT/loaded-runtime-images.tsv"
[[ -e "$record_file" ]] || exit 0
[[ -f "$record_file" && ! -L "$record_file" ]] || exit 2
[[ "$(stat -c '%u:%g' "$record_file")" == "0:0" && "$(stat -c '%a' "$record_file")" == "600" ]] || exit 2

while IFS='|' read -r image image_id recorded_sha; do
  [[ "$recorded_sha" == "$source_sha" ]] || exit 2
  case "$image" in
    "autonomia-sell-preview-sell:$source_sha"|"autonomia-sell-preview-domain:$source_sha"|"autonomia-sell-preview-base-alpine:$source_sha") ;;
    *) echo "unexpected image ownership record" >&2; exit 2 ;;
  esac
  [[ -z "$image_id" || "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 2
  if docker image inspect "$image" >/dev/null 2>&1; then
    current_id="$(docker image inspect --format '{{.Id}}' "$image")"
    [[ -z "$image_id" || "$current_id" == "$image_id" ]] || exit 2
    [[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" == "$source_sha" ]] || exit 2
    if docker ps -aq --filter "ancestor=$image" | grep -q .; then
      echo "refusing to discard ownership record while a container uses $image" >&2
      exit 1
    fi
    docker image rm "$image" >/dev/null
  fi
done <"$record_file"
rm -f -- "$record_file"
echo "removed only unused image tags recorded by Issue #77"
