#!/usr/bin/env bash
set -euo pipefail

ACTIONLINT_VERSION=1.7.12
os="$(uname -s | tr '[:upper:]' '[:lower:]')"
arch="$(uname -m)"

case "${os}_${arch}" in
  linux_x86_64)
    asset="actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz"
    checksum="8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"
    ;;
  darwin_arm64)
    asset="actionlint_${ACTIONLINT_VERSION}_darwin_arm64.tar.gz"
    checksum="aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f"
    ;;
  *)
    echo "Unsupported actionlint platform: ${os}_${arch}" >&2
    exit 2
    ;;
esac

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
archive="$workdir/$asset"
curl --fail --silent --show-error --location --retry 3 \
  "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/${asset}" \
  --output "$archive"

if [ "$os" = linux ]; then
  printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status
else
  actual="$(shasum -a 256 "$archive" | awk '{print $1}')"
  test "$actual" = "$checksum"
fi

tar -xzf "$archive" -C "$workdir" actionlint
"$workdir/actionlint" "$@"
