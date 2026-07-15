#!/usr/bin/env bash
set -euo pipefail

: "${E2E_RUNTIME_EVIDENCE_DIR:?E2E_RUNTIME_EVIDENCE_DIR is required}"

staged="$E2E_RUNTIME_EVIDENCE_DIR"
target=/evidence/current
backup=/evidence/previous
failed=/evidence/failed
rm -rf "$staged"
mkdir -p "$staged"

promote_failure() {
  status=$?
  rm -rf "$failed"
  if [[ -d "$staged" ]]; then
    mv "$staged" "$failed"
  fi
  echo "runtime-preview E2E failed; diagnostic evidence preserved in /evidence/failed" >&2
  exit "$status"
}
trap promote_failure ERR

corepack pnpm exec playwright test --config=playwright.runtime-preview.config.ts
trap - ERR

rm -rf "$backup"
rm -rf "$failed"
if [[ -d "$target" ]]; then
  mv "$target" "$backup"
fi
mv "$staged" "$target"
rm -rf "$backup"

echo "runtime-preview E2E evidence promoted to /evidence/current"
