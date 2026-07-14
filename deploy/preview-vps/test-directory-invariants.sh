#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

install -d -m 0700 "$tmp/run/config" "$tmp/run/artifacts"
links="$(stat -c '%h' "$tmp/run" 2>/dev/null || stat -f '%l' "$tmp/run")"
(( links > 1 )) || {
  echo "Fixture expected a populated Linux directory to have multiple links" >&2
  exit 1
}

if grep -E 'stat .*%h.*"\$run_root"' "$here/activate-preview.sh" >/dev/null; then
  echo "activate-preview.sh incorrectly requires a run directory link count of one" >&2
  exit 1
fi
if grep -E 'stat .*%h.*"\$output_dir"' "$here/generate-preview-env.sh" >/dev/null; then
  echo "generate-preview-env.sh incorrectly requires an env directory link count of one" >&2
  exit 1
fi

grep -Fq "%u:%g:%a:%h' \"\$lock_file\"" "$here/activate-preview.sh" || {
  echo "Sensitive lock file lost its single-link invariant" >&2
  exit 1
}
grep -Fq "%h' \"\$record_file\"" "$here/remove-loaded-images.sh" || {
  echo "Loaded-image record lost its single-link invariant" >&2
  exit 1
}

install -d -m 0700 "$tmp/tree"
printf 'payload\n' >"$tmp/tree/payload"
(cd "$tmp/tree" && sha256sum payload >SHA256SUMS)
ln -s "$tmp/tree" "$tmp/tree-link"
if bash "$here/validate-root-tree.sh" "$tmp/tree-link" >/dev/null 2>&1; then
  echo "Tree validator accepted a symlink root" >&2
  exit 1
fi

echo "Directory and sensitive-file link invariants: passed"
