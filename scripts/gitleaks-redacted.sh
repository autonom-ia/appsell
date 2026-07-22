#!/usr/bin/env bash
set -euo pipefail

repo_path="${1:-.}"
gitleaks_bin="${GITLEAKS_BIN:-gitleaks}"
artifact_dir="${GITLEAKS_ARTIFACT_DIR:-${repo_path}/.harness-ci-artifacts}"
scan_mode="${GITLEAKS_MODE:-full}"

if ! command -v "$gitleaks_bin" >/dev/null 2>&1; then
  echo "ERROR: Gitleaks nao esta disponivel; secret scan nao executado." >&2
  exit 2
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq nao esta disponivel; nao e possivel sanitizar o relatorio Gitleaks." >&2
  exit 2
fi

scratch_dir="$(mktemp -d)"
raw_report="${scratch_dir}/gitleaks-report.json"
raw_log="${scratch_dir}/gitleaks.log"
trap 'rm -f "$raw_report" "$raw_log"; rmdir "$scratch_dir" 2>/dev/null || true' EXIT

scan_status=0
gitleaks_args=(
  git
  --no-banner
  --redact=100
  --report-format=json
  --report-path "$raw_report"
)
case "$scan_mode" in
  full) ;;
  diff)
    base_sha="${GITLEAKS_BASE_SHA:-}"
    head_sha="${GITLEAKS_HEAD_SHA:-HEAD}"
    if [[ ! "$base_sha" =~ ^[0-9a-f]{7,40}$ ]]; then
      echo "ERROR: GITLEAKS_BASE_SHA inválido para scan de diff." >&2
      exit 2
    fi
    if [[ "$head_sha" == "HEAD" ]]; then
      head_sha="$(git -C "$repo_path" rev-parse HEAD)"
    fi
    if [[ ! "$head_sha" =~ ^[0-9a-f]{7,40}$ ]]; then
      echo "ERROR: GITLEAKS_HEAD_SHA inválido para scan de diff." >&2
      exit 2
    fi
    gitleaks_args+=("--log-opts=${base_sha}..${head_sha}")
    ;;
  *)
    echo "ERROR: GITLEAKS_MODE deve ser diff ou full." >&2
    exit 2
    ;;
esac
gitleaks_args+=("$repo_path")
"$gitleaks_bin" "${gitleaks_args[@]}" >"$raw_log" 2>&1 || scan_status=$?

if [ "$scan_status" -eq 0 ]; then
  rm -f "${artifact_dir}/gitleaks-findings.txt"
  echo "Gitleaks: nenhum segredo detectado."
  exit 0
fi

if [ "$scan_status" -ne 1 ] || ! jq -e 'type == "array"' "$raw_report" >/dev/null 2>&1; then
  echo "ERROR: Gitleaks falhou sem relatorio sanitizavel (exit=${scan_status})." >&2
  exit 2
fi

mkdir -p "$artifact_dir"
safe_report="${artifact_dir}/gitleaks-findings.txt"
finding_count="$(jq 'length' "$raw_report")"
{
  echo "Gitleaks encontrou ${finding_count} possivel(is) segredo(s)."
  echo "Valores, nomes de arquivo, linhas, commits e fingerprints foram removidos."
  echo "Arquivos afetados: [REDACTED]"
} > "$safe_report"

cat "$safe_report"
echo "ERROR: secret scan bloqueado; consulte apenas o relatorio sanitizado." >&2
exit 1
