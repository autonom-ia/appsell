#!/usr/bin/env bash
set -euo pipefail

: "${E2E_REAL_EVIDENCE_DIR:?E2E_REAL_EVIDENCE_DIR is required}"

staged="$E2E_REAL_EVIDENCE_DIR"
target="/evidence/current"
backup="/evidence/previous"
rm -rf "$staged"
mkdir -p "$staged"

corepack pnpm exec playwright test --config=playwright.identity-real.config.ts
node deploy/preview-vps/verify-redis.mjs

rm -rf "$backup"
if [[ -d "$target" ]]; then
  mv "$target" "$backup"
fi
mv "$staged" "$target"
rm -rf "$backup"

echo "VPS E2E evidence promoted to /evidence/current."
