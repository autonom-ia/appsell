#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "Usage: $0 OUTPUT_ENV APPSELL_SHA AUTH_SHA FOUNDATION_SHA" >&2
  exit 2
fi

output="$1"
appsell_sha="$2"
auth_sha="$3"
foundation_sha="$4"

for sha in "$appsell_sha" "$auth_sha" "$foundation_sha"; do
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "Every source SHA must contain 40 lowercase hex characters" >&2; exit 2; }
done
[[ "$output" =~ ^/opt/autonomia-sell-preview/issue-66/[0-9a-f]{12}/preview\.env$ ]] || {
  echo "OUTPUT_ENV must use the exact Issue #66 preview root" >&2
  exit 2
}
[[ "${appsell_sha:0:12}" == "$(basename "$(dirname "$output")")" ]] || {
  echo "OUTPUT_ENV directory must match the Autonom.ia Sell SHA" >&2
  exit 2
}
config_dir="$(dirname "$output")/config"
# shellcheck source=docker-local-guard.sh
source "$config_dir/docker-local-guard.sh"
pin_local_preview_docker
output_dir="$(dirname "$output")"
[[ -d "$output_dir" && ! -L "$output_dir" ]] || { echo "Invalid preview env parent directory" >&2; exit 2; }
[[ "$(stat -c '%u:%g:%a' "$output_dir")" == "0:0:700" ]] || {
  echo "Preview env parent must be root:root 0700" >&2
  exit 2
}
[[ ! -e "$output" && ! -L "$output" ]] || { echo "Refusing to overwrite $output" >&2; exit 2; }

sell_image="autonomia-sell-preview-sell:$appsell_sha"
domain_image="autonomia-sell-preview-domain:$appsell_sha"
alpine_image="autonomia-sell-preview-base-alpine:$appsell_sha"
postgres_image="autonomia-sell-preview-base-postgres:$appsell_sha"
redis_image="autonomia-sell-preview-base-redis:$appsell_sha"
auth_image="autonomia-identity-auth-preview:$auth_sha"
foundation_image="autonomia-identity-foundation-preview:$foundation_sha"
for record in \
  "$sell_image|$appsell_sha" \
  "$domain_image|$appsell_sha" \
  "$alpine_image|$appsell_sha" \
  "$postgres_image|$appsell_sha" \
  "$redis_image|$appsell_sha" \
  "$auth_image|$auth_sha" \
  "$foundation_image|$foundation_sha"; do
  IFS='|' read -r image sha <<<"$record"
  [[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" == "$sha" ]] || exit 2
done

short_sha="${appsell_sha:0:12}"
expires_at="$(date -u -d '+48 hours' '+%Y-%m-%dT%H:%M:%SZ')"

hex() { openssl rand -hex "$1"; }
b64() { openssl rand -base64 "$1" | tr -d '\n'; }
b64url() { openssl rand -base64 "$1" | tr '+/' '-_' | tr -d '=\n'; }

domain_hmac="$(b64 32)"
vault_key="$(b64 32)"
context_key_b64="$(b64 32)"
context_key_hex="$(printf '%s' "$context_key_b64" | openssl base64 -d -A | od -An -tx1 | tr -d ' \n')"

umask 077
tmp_output="$(mktemp "$output_dir/.preview.env.XXXXXX")"
cleanup() { rm -f -- "$tmp_output"; }
trap cleanup EXIT
{
  printf 'PREVIEW_PROJECT_NAME=autonomia_sell_preview_66_%s\n' "$short_sha"
  printf 'PREVIEW_EXPIRES_AT=%s\n' "$expires_at"
  printf 'APPSELL_SOURCE_SHA=%s\n' "$appsell_sha"
  printf 'IDENTITY_AUTH_SOURCE_SHA=%s\n' "$auth_sha"
  printf 'IDENTITY_FOUNDATION_SOURCE_SHA=%s\n' "$foundation_sha"
  printf 'APPSELL_SELL_IMAGE=%s\n' "$sell_image"
  printf 'APPSELL_DOMAIN_IMAGE=%s\n' "$domain_image"
  printf 'APPSELL_ALPINE_IMAGE=%s\n' "$alpine_image"
  printf 'APPSELL_POSTGRES_IMAGE=%s\n' "$postgres_image"
  printf 'APPSELL_REDIS_IMAGE=%s\n' "$redis_image"
  printf 'IDENTITY_AUTH_IMAGE=%s\n' "$auth_image"
  printf 'IDENTITY_FOUNDATION_IMAGE=%s\n' "$foundation_image"
  printf 'APPSELL_SELL_IMAGE_ID=%s\n' "$(docker image inspect --format '{{.Id}}' "$sell_image")"
  printf 'APPSELL_DOMAIN_IMAGE_ID=%s\n' "$(docker image inspect --format '{{.Id}}' "$domain_image")"
  printf 'APPSELL_ALPINE_IMAGE_ID=%s\n' "$(docker image inspect --format '{{.Id}}' "$alpine_image")"
  printf 'APPSELL_POSTGRES_IMAGE_ID=%s\n' "$(docker image inspect --format '{{.Id}}' "$postgres_image")"
  printf 'APPSELL_REDIS_IMAGE_ID=%s\n' "$(docker image inspect --format '{{.Id}}' "$redis_image")"
  printf 'IDENTITY_AUTH_IMAGE_ID=%s\n' "$(docker image inspect --format '{{.Id}}' "$auth_image")"
  printf 'IDENTITY_FOUNDATION_IMAGE_ID=%s\n' "$(docker image inspect --format '{{.Id}}' "$foundation_image")"
  printf 'IDENTITY_DATABASE_NAME=identity_oauth_local_%s\n' "$(hex 16)"
  printf 'APPSELL_POSTGRES_SUPERUSER_PASSWORD=%s\n' "$(hex 24)"
  printf 'APPSELL_MIGRATOR_PASSWORD=%s\n' "$(hex 24)"
  printf 'APPSELL_RUNTIME_PASSWORD=%s\n' "$(hex 24)"
  printf 'IDENTITY_POSTGRES_PASSWORD=%s\n' "$(hex 24)"
  printf 'REDIS_PASSWORD=%s\n' "$(hex 24)"
  printf 'IDENTITY_DEMO_PASSWORD=Vps-%s\n' "$(b64url 24)"
  printf 'IDENTITY_SESSION_LOCAL_KEY_B64=%s\n' "$(b64 32)"
  printf 'APPSELL_IDENTITY_E2E_CONTROL_TOKEN=%s\n' "$(b64url 32)"
  printf 'APPSELL_REDIS_PREFIX=appsell:preview:issue66:%s:\n' "$short_sha"
  printf 'APPSELL_SESSION_HMAC_KEY_B64=%s\n' "$(b64 32)"
  printf 'APPSELL_CSRF_HMAC_KEY_B64=%s\n' "$(b64 32)"
  printf 'APPSELL_VAULT_LOCAL_KEYRING_B64_JSON=[{"id":"preview-v1","key":"%s","active":true}]\n' "$vault_key"
  printf 'APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON=[{"id":"preview-v1","key":"%s","active":true}]\n' "$domain_hmac"
  printf 'APPSELL_API_CONTEXT_HMAC_KEY_B64=%s\n' "$context_key_b64"
  printf 'APPSELL_CONTEXT_SIGNING_KEY_HEX=%s\n' "$context_key_hex"
  printf 'APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX=%s\n' "$(hex 32)"
  printf 'APPSELL_SERVICE_CREDENTIAL_B64=%s\n' "$(b64 32)"
} >"$tmp_output"
chown root:root "$tmp_output"
chmod 600 "$tmp_output"
sync -f "$tmp_output"
[[ ! -e "$output" && ! -L "$output" ]] || { echo "Preview env appeared during creation" >&2; exit 2; }
mv -T -- "$tmp_output" "$output"
tmp_output=""
trap - EXIT
echo "Created an ephemeral preview env at $output (root:root 0600)."
