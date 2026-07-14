#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 COMPOSE_FILE ENV_FILE" >&2
  exit 2
fi

compose_file="$(realpath -e "$1")"
env_file="$2"
compose=(docker compose --env-file "$env_file" -f "$compose_file")
cleanup_on_error() {
  "$(dirname "$compose_file")/teardown.sh" "$compose_file" "$env_file" || true
}
trap cleanup_on_error ERR

"$(dirname "$compose_file")/preflight.sh" "$compose_file" "$env_file"
"$(dirname "$compose_file")/schedule-expiry.sh" "$compose_file" "$env_file"
"${compose[@]}" up -d --no-build --wait
trap - ERR
echo "Issue #66 preview started from reviewed CI artifact image IDs; no build or registry login ran on the VPS."
