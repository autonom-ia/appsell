#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 COMPOSE_FILE ENV_FILE" >&2
  exit 2
fi
[[ "${AUTONOMIA_PREVIEW_ORCHESTRATED:-}" == "1" ]] || {
  echo "Use activate-preview.sh so post-start verification failures roll back immediately" >&2
  exit 2
}

compose_file="$(realpath -e "$1")"
env_file="$2"
config_dir="$(dirname "$compose_file")"
# shellcheck source=docker-local-guard.sh
source "$config_dir/docker-local-guard.sh"
pin_local_preview_docker
cleanup_on_error() {
  "$config_dir/teardown.sh" "$compose_file" "$env_file" || true
}
trap cleanup_on_error ERR

"$config_dir/preflight.sh" "$compose_file" "$env_file"
# shellcheck source=load-preview-env.sh
source "$config_dir/load-preview-env.sh"
load_preview_env "$env_file"
unset COMPOSE_PROJECT_NAME COMPOSE_FILE COMPOSE_PROFILES COMPOSE_ENV_FILES
compose=(docker compose --project-name "$PREVIEW_PROJECT_NAME" --env-file "$env_file" -f "$compose_file")
"$config_dir/schedule-expiry.sh" "$compose_file" "$env_file"
"${compose[@]}" up -d --no-build --pull never --wait
trap - ERR
echo "Issue #66 preview started from reviewed CI artifact image IDs; no build or registry login ran on the VPS."
