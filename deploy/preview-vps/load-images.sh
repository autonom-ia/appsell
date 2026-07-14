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

artifacts="$run_root/artifacts"
validator="$run_root/config/validate-root-tree.sh"
[[ -f "$validator" && ! -L "$validator" ]] || exit 2
bash "$validator" "$artifacts"

records=(
  "sell-$appsell_sha.tar|autonomia-sell-preview-sell:$appsell_sha|$appsell_sha"
  "domain-$appsell_sha.tar|autonomia-sell-preview-domain:$appsell_sha|$appsell_sha"
  "auth-$auth_sha.tar|autonomia-identity-auth-preview:$auth_sha|$auth_sha"
  "foundation-$foundation_sha.tar|autonomia-identity-foundation-preview:$foundation_sha|$foundation_sha"
)
for record in "${records[@]}"; do
  IFS='|' read -r file image sha <<<"$record"
  [[ -f "$artifacts/$file" ]] || { echo "Missing $file" >&2; exit 2; }
  if tar -tf "$artifacts/$file" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
    echo "Unsafe path in Docker TAR: $file" >&2
    exit 2
  fi
  manifest="$(tar -xOf "$artifacts/$file" manifest.json)"
  jq -e --arg image "$image" 'length == 1 and .[0].RepoTags == [$image] and (.[] | .Config | type == "string")' <<<"$manifest" >/dev/null || {
    echo "Docker TAR must contain exactly the expected RepoTag: $image" >&2
    exit 2
  }
  config_path="$(jq -r '.[0].Config' <<<"$manifest")"
  [[ "$config_path" =~ ^[0-9a-f]{64}\.json$ ]] || exit 2
  tar -xOf "$artifacts/$file" "$config_path" \
    | jq -e --arg sha "$sha" '.config.Labels["org.opencontainers.image.revision"] == $sha' >/dev/null || {
      echo "Docker TAR config revision mismatch: $image" >&2
      exit 2
    }
  if docker image inspect "$image" >/dev/null 2>&1; then
    echo "Refusing image tag collision: $image" >&2
    exit 2
  fi
done

for record in "${records[@]}"; do
  IFS='|' read -r file image sha <<<"$record"
  docker image load --input "$artifacts/$file" >/dev/null
  [[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" == "$sha" ]] || {
    echo "Loaded image revision mismatch: $image" >&2
    exit 1
  }
done

echo "Loaded four reviewed CI artifacts without building or pulling on the VPS."
