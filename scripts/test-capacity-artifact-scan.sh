#!/usr/bin/env bash
set -euo pipefail

directory="$(mktemp -d "${TMPDIR:-/tmp}/appsell-capacity-scan.XXXXXX")"
trap 'rm -rf -- "$directory"' EXIT
artifact="$directory/artifact.json"
context_key="capacity-context-key-canary-123456"
hmac_keyring="capacity-hmac-keyring-canary-123456"
database_url="postgresql://user:capacity-password-canary@127.0.0.1/appsell"
email="capacity-buyer-canary@autonomia.site"
printf '%s\n' "$context_key" "$hmac_keyring" "$database_url" "$email" >"$artifact"

if output="$({
  APPSELL_CONTEXT_SIGNING_KEY_HEX="$context_key" \
    APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON="$hmac_keyring" \
    APPSELL_MIGRATOR_DATABASE_URL="$database_url" \
    python3 scripts/assert_capacity_artifact_safe.py "$artifact"
} 2>&1)"; then
  echo "capacity scanner accepted adversarial artifact" >&2
  exit 1
fi
for canary in "$context_key" "$hmac_keyring" "$database_url" "$email"; do
  if [[ "$output" == *"$canary"* ]]; then
    echo "capacity scanner leaked a canary value" >&2
    exit 1
  fi
done

printf '%s\n' '{"status":"failed","failure":"startup_failed"}' >"$artifact"
python3 scripts/assert_capacity_artifact_safe.py "$artifact" >/dev/null
echo "capacity artifact scanner console test: passed"
