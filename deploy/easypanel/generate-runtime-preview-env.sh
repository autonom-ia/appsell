#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 2 ]] || {
  echo "Usage: $0 OUTPUT_DIRECTORY APPSELL_SOURCE_SHA" >&2
  exit 2
}

output_directory="$1"
source_sha="$2"
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid AppSell source SHA" >&2; exit 2; }
[[ ! -e "$output_directory" && ! -L "$output_directory" ]] || {
  echo "refusing to reuse runtime-preview env directory" >&2
  exit 2
}
public_ipv4="${APPSELL_RUNTIME_PREVIEW_PUBLIC_IPV4:-}"
[[ -n "$public_ipv4" && "$public_ipv4" != *$'\n'* && "$public_ipv4" != *$'\r'* ]] || {
  echo "APPSELL_RUNTIME_PREVIEW_PUBLIC_IPV4 is required: the approved public IPv4 of the host" >&2
  exit 2
}
for command_name in docker jq openssl xxd base64; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "missing required command: $command_name" >&2
    exit 2
  }
done

service_env() {
  local service="$1" key="$2" value
  value="$(docker service inspect "$service" \
    --format '{{range .Spec.TaskTemplate.ContainerSpec.Env}}{{println .}}{{end}}' \
    | awk -F= -v key="$key" '$1==key {sub(/^[^=]*=/,""); print; found++} END {if(found!=1) exit 3}')" || {
      echo "missing or duplicate $key in exclusive service $service" >&2
      exit 2
    }
  [[ -n "$value" && "$value" != *$'\n'* && "$value" != *$'\r'* ]] || {
    echo "invalid $key in exclusive service $service" >&2
    exit 2
  }
  printf '%s' "$value"
}

postgres_user="$(service_env sell-autonomia_postgres POSTGRES_USER)"
postgres_secret="$(service_env sell-autonomia_postgres POSTGRES_PASSWORD)"
postgres_database="$(service_env sell-autonomia_postgres POSTGRES_DB)"
redis_secret="$(service_env sell-autonomia_redis REDIS_PASSWORD)"

hex_24() { openssl rand -hex 24; }
hex_32() { openssl rand -hex 32; }
b64_32() { openssl rand -base64 32 | tr -d '\n'; }

migrator_secret="$(hex_24)"
runtime_secret="$(hex_24)"
context_hex="$(hex_32)"
bootstrap_hex="$(hex_32)"
context_b64="$(printf '%s' "$context_hex" | xxd -r -p | base64 | tr -d '\n')"
internal_key="$(b64_32)"
service_credential="$(b64_32)"
session_key="$(b64_32)"
csrf_key="$(b64_32)"
vault_key="$(b64_32)"

unique_values=(
  "$migrator_secret" "$runtime_secret" "$context_hex" "$bootstrap_hex"
  "$internal_key" "$service_credential" "$session_key" "$csrf_key" "$vault_key"
)
for ((left=0; left<${#unique_values[@]}; left++)); do
  for ((right=left+1; right<${#unique_values[@]}; right++)); do
    [[ "${unique_values[left]}" != "${unique_values[right]}" ]] || {
      echo "generated runtime-preview secrets were unexpectedly reused" >&2
      exit 1
    }
  done
done

internal_keyring="$(jq -cn --arg key "$internal_key" \
  '[{id:"runtime-v1",key:$key,active:true}]')"
vault_keyring="$(jq -cn --arg key "$vault_key" \
  '[{id:"runtime-v1",key:$key,active:true}]')"

umask 077
install -d -m 0700 "$output_directory"
output_file="$output_directory/runtime-preview.env"
trap 'rm -f -- "$output_file"' ERR INT TERM
{
  printf 'APPSELL_SOURCE_SHA=%s\n' "$source_sha"
  printf 'APPSELL_DOMAIN_IMAGE=autonomia-sell-preview-domain:%s\n' "$source_sha"
  printf 'APPSELL_SELL_IMAGE=autonomia-sell-preview-sell:%s\n' "$source_sha"
  printf 'APPSELL_ALPINE_IMAGE=autonomia-sell-preview-base-alpine:%s\n' "$source_sha"
  printf 'APPSELL_PUBLIC_ORIGIN=https://sell.autonomia.site\n'
  printf 'APPSELL_RUNTIME_PREVIEW_PUBLIC_IPV4=%s\n' "$public_ipv4"
  printf 'APPSELL_POSTGRES_ADMIN_USER=%s\n' "$postgres_user"
  printf 'APPSELL_POSTGRES_ADMIN_PASSWORD=%s\n' "$postgres_secret"
  printf 'APPSELL_POSTGRES_DEFAULT_DATABASE=%s\n' "$postgres_database"
  printf 'APPSELL_REDIS_PASSWORD=%s\n' "$redis_secret"
  printf 'APPSELL_MIGRATOR_PASSWORD=%s\n' "$migrator_secret"
  printf 'APPSELL_RUNTIME_PASSWORD=%s\n' "$runtime_secret"
  printf 'APPSELL_CONTEXT_SIGNING_KEY_HEX=%s\n' "$context_hex"
  printf 'APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX=%s\n' "$bootstrap_hex"
  printf 'APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON=%s\n' "$internal_keyring"
  printf 'APPSELL_API_CONTEXT_HMAC_KEY_B64=%s\n' "$context_b64"
  printf 'APPSELL_SERVICE_CREDENTIAL_B64=%s\n' "$service_credential"
  printf 'APPSELL_SESSION_HMAC_KEY_B64=%s\n' "$session_key"
  printf 'APPSELL_CSRF_HMAC_KEY_B64=%s\n' "$csrf_key"
  printf 'APPSELL_VAULT_LOCAL_KEYRING_B64_JSON=%s\n' "$vault_keyring"
} >"$output_file"
chmod 0600 "$output_file"
trap - ERR INT TERM

echo "runtime-preview env generated at a mode-0700 directory with a mode-0600 file"
