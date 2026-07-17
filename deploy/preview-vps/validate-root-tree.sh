#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 DIRECTORY" >&2
  exit 2
fi
[[ -d "$1" && ! -L "$1" ]] || { echo "Tree root must be a non-symlink directory" >&2; exit 2; }
root="$(realpath -e "$1")"
[[ "$(stat -c '%u:%g' "$root")" == "0:0" ]] || { echo "Tree root must be root-owned" >&2; exit 2; }
if find "$root" -xdev \( -type l -o ! -user root -o -perm /022 \) -print -quit | grep -q .; then
  echo "Tree contains a symlink, non-root owner or group/world-writable entry" >&2
  exit 2
fi
if find "$root" -xdev -type f -links +1 -print -quit | grep -q .; then
  echo "Tree contains a hard-linked regular file" >&2
  exit 2
fi
[[ -f "$root/SHA256SUMS" && ! -L "$root/SHA256SUMS" ]] || exit 2
actual="$(cd "$root" && find . -xdev -type f ! -path './SHA256SUMS' -printf '%P\n' | LC_ALL=C sort)"
listed="$(awk '
  !/^[0-9a-f]{64}  [^/].*$/ { exit 2 }
  { name=substr($0,67); if (name ~ /(^|\/)\.\.($|\/)/ || seen[name]++) exit 2; print name }
' "$root/SHA256SUMS" | LC_ALL=C sort)" || { echo "Malformed or duplicate checksum manifest" >&2; exit 2; }
[[ "$actual" == "$listed" ]] || { echo "Checksum manifest does not exactly cover the tree" >&2; exit 2; }
(cd "$root" && sha256sum --strict -c SHA256SUMS >/dev/null)
