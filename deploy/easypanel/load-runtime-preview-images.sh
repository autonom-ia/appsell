#!/usr/bin/env bash
set -euo pipefail
case $- in *x*) set +x ;; esac

[[ $# -eq 2 ]] || { echo "Usage: $0 RUN_ROOT APPSELL_SOURCE_SHA" >&2; exit 2; }
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-preview-common.sh
source "$here/runtime-preview-common.sh"
runtime_preview_require_root
runtime_preview_require_commands docker jq tar sha256sum stat realpath df
runtime_preview_assert_run_root "$1" "$2"
source_sha="$2"
artifacts="$RUNTIME_PREVIEW_RUN_ROOT/artifacts"
runtime_preview_assert_protected_directory "$artifacts"
runtime_preview_assert_protected_file "$artifacts/SHA256SUMS"
(cd "$artifacts" && sha256sum --check SHA256SUMS) >/dev/null

records=(
  "sell-$source_sha.tar|autonomia-sell-preview-sell:$source_sha"
  "domain-$source_sha.tar|autonomia-sell-preview-domain:$source_sha"
  "base-alpine-$source_sha.tar|autonomia-sell-preview-base-alpine:$source_sha"
)
total_bytes=0
for record in "${records[@]}"; do
  IFS='|' read -r filename image <<<"$record"
  file="$artifacts/$filename"
  runtime_preview_assert_protected_file "$file"
  size="$(stat -c '%s' "$file")"
  (( size > 0 && size <= 8 * 1024 * 1024 * 1024 )) || exit 2
  total_bytes=$((total_bytes + size))
  if tar -tf "$file" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
    echo "unsafe path in Docker TAR: $filename" >&2
    exit 2
  fi
  manifest="$(tar -xOf "$file" manifest.json)"
  jq -e --arg image "$image" 'length == 1 and .[0].RepoTags == [$image] and (.[0].Config | type == "string")' <<<"$manifest" >/dev/null || exit 2
  config_path="$(jq -r '.[0].Config' <<<"$manifest")"
  [[ "$config_path" =~ ^[0-9a-f]{64}\.json$ ]] || exit 2
  image_config="$(tar -xOf "$file" "$config_path")"
  jq -e --arg sha "$source_sha" \
    '.config.Labels["org.opencontainers.image.revision"] == $sha' <<<"$image_config" >/dev/null || exit 2
  jq -e '.config.Labels["com.autonomia.issue"] == "77"' <<<"$image_config" >/dev/null || {
    echo "runtime image is not bound to Issue #77: $filename" >&2
    exit 2
  }
  if docker image inspect "$image" >/dev/null 2>&1; then
    echo "refusing image tag collision: $image" >&2
    exit 2
  fi
done
(( total_bytes <= 16 * 1024 * 1024 * 1024 )) || exit 2
docker_root="$(docker info --format '{{.DockerRootDir}}')"
available="$(df -B1 --output=avail "$docker_root" | tail -1 | tr -d ' ')"
(( available >= 20 * 1024 * 1024 * 1024 + 3 * total_bytes )) || {
  echo "insufficient Docker disk headroom" >&2
  exit 2
}

record_file="$RUNTIME_PREVIEW_RUN_ROOT/loaded-runtime-images.tsv"
[[ ! -e "$record_file" && ! -L "$record_file" ]] || { echo "loaded-image record collision" >&2; exit 2; }
install -o root -g root -m 0600 /dev/null "$record_file"
cleanup() {
  status=$?
  trap - ERR INT TERM EXIT
  (( status != 0 )) || status=1
  "$here/remove-runtime-preview-images.sh" "$RUNTIME_PREVIEW_RUN_ROOT" "$source_sha" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup ERR INT TERM EXIT
for record in "${records[@]}"; do
  IFS='|' read -r filename image <<<"$record"
  printf '%s||%s\n' "$image" "$source_sha" >>"$record_file"
  sync -f "$record_file"
  docker image load --input "$artifacts/$filename" >/dev/null
  image_id="$(docker image inspect --format '{{.Id}}' "$image")"
  [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 1
  [[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" == "$source_sha" ]] || exit 1
  printf '%s|%s|%s\n' "$image" "$image_id" "$source_sha" >>"$record_file"
  sync -f "$record_file"
done
trap - ERR INT TERM EXIT
echo "three reviewed Issue #77 images loaded without build or pull"
