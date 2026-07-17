#!/usr/bin/env bash
set -euo pipefail

project="appsell_capacity_startup_${RANDOM}_$$"
port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
report="${APPSELL_CAPACITY_STARTUP_FAULT_REPORT:-$PWD/tmp/capacity-postgres-fault.json}"
superuser_secret="$(openssl rand -hex 24)"
migrator_secret="$(openssl rand -hex 24)"
runtime_secret="$(openssl rand -hex 24)"
cleanup_started="false"

cleanup() {
  local original_status="${1:-$?}"
  if [[ "$cleanup_started" == "true" ]]; then return "$original_status"; fi
  cleanup_started="true"
  trap - EXIT INT TERM
  local down_failed="false"
  if ! docker compose -p "$project" down --volumes --remove-orphans >/dev/null 2>&1; then
    down_failed="true"
    original_status=1
  fi
  local containers networks volumes
  containers="$(docker ps -aq --filter "label=com.docker.compose.project=$project")" || original_status=1
  networks="$(docker network ls -q --filter "label=com.docker.compose.project=$project")" || original_status=1
  volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=$project")" || original_status=1
  REPORT_PATH="$report" STATUS="$original_status" DOWN_FAILED="$down_failed" \
    CONTAINERS="$containers" NETWORKS="$networks" VOLUMES="$volumes" python3 -c '
import json, os
from pathlib import Path
path = Path(os.environ["REPORT_PATH"])
try:
    report = json.loads(path.read_text(encoding="utf-8"))
except (FileNotFoundError, json.JSONDecodeError):
    report = {"schema_version": 1, "phase": "postgres_startup", "status": "failed", "code": "startup_evidence_unavailable", "calibration_eligible": False}
down_succeeded = os.environ["DOWN_FAILED"] == "false"
clean = down_succeeded and not any((os.environ["CONTAINERS"], os.environ["NETWORKS"], os.environ["VOLUMES"]))
report["docker_teardown"] = {
    "status": "passed" if clean else "failed",
    "down_succeeded": down_succeeded,
    "containers_remaining": len(os.environ["CONTAINERS"].split()),
    "networks_remaining": len(os.environ["NETWORKS"].split()),
    "volumes_remaining": len(os.environ["VOLUMES"].split()),
}
if int(os.environ["STATUS"]) != 0 or not clean:
    report["status"] = "failed"
    report["calibration_eligible"] = False
path.parent.mkdir(parents=True, exist_ok=True)
temporary = path.with_suffix(path.suffix + ".tmp")
temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(path)
' || original_status=1
  if [[ -f "$report" ]] && ! python3 scripts/assert_capacity_artifact_safe.py "$report"; then
    original_status=1
  fi
  exit "$original_status"
}
trap 'status=$?; cleanup "$status"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p "$(dirname "$report")"
rm -f -- "$report"
export APPSELL_POSTGRES_PORT="$port"
export APPSELL_POSTGRES_SUPERUSER_PASSWORD="$superuser_secret"
export APPSELL_MIGRATOR_PASSWORD="$migrator_secret"
export APPSELL_RUNTIME_PASSWORD="$runtime_secret"
export APPSELL_API_ENVIRONMENT="ci"
export APPSELL_MIGRATOR_DATABASE_URL="postgresql+psycopg://appsell_migrator:${migrator_secret}@127.0.0.1:${port}/appsell"
export APPSELL_CAPACITY_FAULT_INJECTION="postgres_restart_once_after_health_v1"
export APPSELL_CAPACITY_FAULT_CONFIRMATION="confirm_disposable_postgres_restart_v1"

if ! docker compose -p "$project" up -d --wait --wait-timeout 60 postgres; then
  uv run --project apps/api python scripts/capacity_postgres_startup.py \
    --project "$project" --report "$report" --capture-compose-failure || true
  exit 1
fi
uv run --project apps/api python scripts/capacity_postgres_startup.py \
  --project "$project" --report "$report"
REPORT_PATH="$report" python3 -c '
import json, os
report = json.load(open(os.environ["REPORT_PATH"], encoding="utf-8"))
assert report["status"] == "passed"
assert report["recovered"] is True
assert report["calibration_eligible"] is False
assert report["fault_injection"]["mode"] == "postgres_restart_once_after_health_v1"
assert report["fault_injection"]["requested"] is True
assert report["fault_injection"]["occurred"] is True
assert report["fault_injection"]["shutdown_observed"] is True
assert report["fault_injection"]["restart_observed"] is True
'
python3 scripts/assert_capacity_artifact_safe.py "$report"
