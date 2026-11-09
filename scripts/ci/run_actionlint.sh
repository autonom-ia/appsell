#!/usr/bin/env bash
# Roda o actionlint fixado por versão e sha256.
#
#   run_actionlint.sh --cache-key        imprime a chave do actions/cache
#   run_actionlint.sh --fetch-only       garante o arquivo no cache e confere
#   run_actionlint.sh <workflow>...      confere e roda o actionlint
#
# APPSELL_PINNED_CACHE_DIR aponta o diretório que o workflow restaura com
# actions/cache (#634). Sem ele, o arquivo vai para um temporário e é baixado
# a cada execução, como antes. O sha256 é conferido em todo caso.
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

if [ "${1:-}" = --cache-key ]; then
  echo "actionlint-${ACTIONLINT_VERSION}-${os}_${arch}-${checksum}"
  exit 0
fi

# shellcheck source=scripts/ci/pinned_download.sh
source "$(dirname "${BASH_SOURCE[0]}")/pinned_download.sh"

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
archive="${APPSELL_PINNED_CACHE_DIR:-$workdir}/$asset"
pinned_fetch \
  "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/${asset}" \
  "$archive"

if [ "$os" = linux ]; then
  verified=true
  printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status || verified=false
else
  actual="$(shasum -a 256 "$archive" | awk '{print $1}')"
  verified=false
  if [ "$actual" = "$checksum" ]; then verified=true; fi
fi
if [ "$verified" != true ]; then
  echo "::error title=Checksum do actionlint não confere::$asset não tem o sha256 fixado. O arquivo não é executado." >&2
  exit 1
fi

if [ "${1:-}" = --fetch-only ]; then
  exit 0
fi

tar -xzf "$archive" -C "$workdir" actionlint
"$workdir/actionlint" "$@"
