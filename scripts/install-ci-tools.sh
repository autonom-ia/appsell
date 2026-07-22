#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Resolved relative to this installed script.
# shellcheck disable=SC1091
source "${script_dir}/ci-tool-versions.env"

group="all"
destination="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/autonomia-harness-tools"

usage() {
  cat <<'EOF'
Uso: install-ci-tools.sh [--group core|security|eval|all] [--dest PATH]

Instala somente no diretório informado. Não usa instalação global.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --group)
      group="${2:-}"
      shift 2
      ;;
    --dest)
      destination="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Opção desconhecida: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

case "$group" in
  core|security|eval|all) ;;
  *)
    echo "--group deve ser core, security, eval ou all." >&2
    exit 1
    ;;
esac

if [[ "$(uname -s)" != "Linux" ]] || [[ "$(uname -m)" != "x86_64" ]]; then
  echo "Este instalador pinado suporta o runner Linux x86_64; use os tools locais em outras plataformas." >&2
  exit 2
fi

mkdir -p "$destination"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

sha256_check() {
  local expected="$1" file="$2"
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s  %s\n' "$expected" "$file" | sha256sum --check --status
  else
    [[ "$(shasum -a 256 "$file" | awk '{print $1}')" == "$expected" ]]
  fi
}

download() {
  local url="$1" expected="$2" output="$3"
  curl --fail --silent --show-error --location --retry 3 "$url" --output "$output"
  sha256_check "$expected" "$output" || {
    echo "Checksum inválido para $(basename "$output")" >&2
    exit 3
  }
}

install_core() {
  local archive

  archive="$work_dir/shellcheck.tar.gz"
  download \
    "https://github.com/koalaman/shellcheck/releases/download/v${SHELLCHECK_VERSION}/shellcheck-v${SHELLCHECK_VERSION}.linux.x86_64.tar.gz" \
    "$SHELLCHECK_LINUX_X64_SHA256" "$archive"
  tar -xzf "$archive" -C "$work_dir"
  cp "$work_dir/shellcheck-v${SHELLCHECK_VERSION}/shellcheck" "$destination/shellcheck"

  archive="$work_dir/bats.tar.gz"
  download \
    "https://github.com/bats-core/bats-core/archive/refs/tags/v${BATS_VERSION}.tar.gz" \
    "$BATS_SOURCE_SHA256" "$archive"
  tar -xzf "$archive" -C "$work_dir"
  cp -R "$work_dir/bats-core-${BATS_VERSION}" "$destination/bats-core"
  ln -sf "$destination/bats-core/bin/bats" "$destination/bats"

  archive="$work_dir/actionlint.tar.gz"
  download \
    "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz" \
    "$ACTIONLINT_LINUX_X64_SHA256" "$archive"
  tar -xzf "$archive" -C "$work_dir" actionlint
  cp "$work_dir/actionlint" "$destination/actionlint"

  archive="$work_dir/zizmor.tar.gz"
  download \
    "https://github.com/zizmorcore/zizmor/releases/download/v${ZIZMOR_VERSION}/zizmor-x86_64-unknown-linux-gnu.tar.gz" \
    "$ZIZMOR_LINUX_X64_SHA256" "$archive"
  tar -xzf "$archive" -C "$work_dir" zizmor
  cp "$work_dir/zizmor" "$destination/zizmor"
}

install_security() {
  local archive

  archive="$work_dir/gitleaks.tar.gz"
  download \
    "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
    "$GITLEAKS_LINUX_X64_SHA256" "$archive"
  tar -xzf "$archive" -C "$work_dir" gitleaks
  cp "$work_dir/gitleaks" "$destination/gitleaks"

  download \
    "https://github.com/google/osv-scanner/releases/download/v${OSV_SCANNER_VERSION}/osv-scanner_linux_amd64" \
    "$OSV_SCANNER_LINUX_X64_SHA256" "$destination/osv-scanner"
}

install_eval() {
  local actual_integrity wrapper
  command -v npm >/dev/null 2>&1 || {
    echo "npm é necessário para o Promptfoo pinado." >&2
    exit 2
  }
  actual_integrity="$(npm view "promptfoo@${PROMPTFOO_VERSION}" dist.integrity)"
  [[ "$actual_integrity" == "$PROMPTFOO_NPM_INTEGRITY" ]] || {
    echo "Integridade npm inesperada para Promptfoo ${PROMPTFOO_VERSION}." >&2
    exit 3
  }
  wrapper="$destination/promptfoo"
  {
    echo '#!/usr/bin/env bash'
    printf 'exec npx --yes --package "promptfoo@%s" promptfoo "$@"\n' "$PROMPTFOO_VERSION"
  } > "$wrapper"
}

case "$group" in
  core) install_core ;;
  security) install_security ;;
  eval) install_eval ;;
  all)
    install_core
    install_security
    install_eval
    ;;
esac

chmod +x "$destination"/*
printf '%s\n' "$destination"
