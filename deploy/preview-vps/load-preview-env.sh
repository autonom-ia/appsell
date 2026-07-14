#!/usr/bin/env bash

load_preview_env() {
  local env_file="$1" key value owner mode canonical
  declare -A seen=()
  canonical="$(realpath -e "$env_file")" || return 2
  [[ "$canonical" =~ ^/opt/autonomia-sell-preview/issue-66/[0-9a-f]{12}/preview\.env$ ]] || {
    echo "Preview env path is outside the Issue #66 root" >&2
    return 2
  }
  owner="$(stat -c '%u:%g' "$canonical")"
  mode="$(stat -c '%a' "$canonical")"
  [[ "$owner" == "0:0" && "$mode" == "600" ]] || {
    echo "Preview env must be root:root 0600" >&2
    return 2
  }
  while IFS='=' read -r key value; do
    [[ -n "$key" && "$key" =~ ^[A-Z][A-Z0-9_]*$ && "$value" != *$'\r'* ]] || {
      echo "Invalid preview env record" >&2
      return 2
    }
    [[ -z "${seen[$key]+present}" ]] || {
      echo "Duplicate preview env key: $key" >&2
      return 2
    }
    seen["$key"]=1
    case "$key" in
      PREVIEW_PROJECT_NAME|PREVIEW_EXPIRES_AT|APPSELL_SOURCE_SHA|IDENTITY_AUTH_SOURCE_SHA|IDENTITY_FOUNDATION_SOURCE_SHA|APPSELL_SELL_IMAGE|APPSELL_DOMAIN_IMAGE|IDENTITY_AUTH_IMAGE|IDENTITY_FOUNDATION_IMAGE|APPSELL_SELL_IMAGE_ID|APPSELL_DOMAIN_IMAGE_ID|IDENTITY_AUTH_IMAGE_ID|IDENTITY_FOUNDATION_IMAGE_ID|IDENTITY_DATABASE_NAME|APPSELL_POSTGRES_SUPERUSER_PASSWORD|APPSELL_MIGRATOR_PASSWORD|APPSELL_RUNTIME_PASSWORD|IDENTITY_POSTGRES_PASSWORD|REDIS_PASSWORD|IDENTITY_DEMO_PASSWORD|IDENTITY_SESSION_LOCAL_KEY_B64|APPSELL_IDENTITY_E2E_CONTROL_TOKEN|APPSELL_REDIS_PREFIX|APPSELL_SESSION_HMAC_KEY_B64|APPSELL_CSRF_HMAC_KEY_B64|APPSELL_VAULT_LOCAL_KEYRING_B64_JSON|APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON|APPSELL_API_CONTEXT_HMAC_KEY_B64|APPSELL_CONTEXT_SIGNING_KEY_HEX|APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX|APPSELL_SERVICE_CREDENTIAL_B64)
        printf -v "$key" '%s' "$value"
        export "$key"
        ;;
      *)
        echo "Unexpected preview env key: $key" >&2
        return 2
        ;;
    esac
  done <"$canonical"
}
