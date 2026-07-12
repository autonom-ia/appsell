#!/usr/bin/env bash
set -euo pipefail

project="appsell_capacity_${RANDOM}_$$"
postgres_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
api_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
storage_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
superuser_secret="$(openssl rand -hex 24)"
migrator_secret="$(openssl rand -hex 24)"
runtime_secret="$(openssl rand -hex 24)"
context_key="$(openssl rand -hex 32)"
bootstrap_key="$(openssl rand -hex 32)"
internal_key_b64="$(openssl rand -base64 32)"
service_credential_b64="$(openssl rand -base64 32)"
storage_access="capacity-$(openssl rand -hex 8)"
storage_secret="$(openssl rand -hex 32)"
report="${APPSELL_CAPACITY_REPORT:-$PWD/tmp/capacity-100.json}"
capacity_executor_timeout_seconds=960
fixture="${report%.json}-fixture.json"
worker_metrics="${report%.json}-worker-rss.json"
vitest_metrics="${report%.json}-vitest-rss.json"
startup_report="${report%.json}-postgres-startup.json"
cleanup_started="false"

mark_report_failed() {
  local exit_code="$1"
  mkdir -p "$(dirname "$report")"
  REPORT_PATH="$report" STARTUP_REPORT_PATH="$startup_report" EXIT_CODE="$exit_code" python3 -c '
import json, os
path = os.environ["REPORT_PATH"]
try:
    with open(path, encoding="utf-8") as source:
        report = json.load(source)
except (FileNotFoundError, json.JSONDecodeError):
    report = {
        "schema_version": 2,
        "mode": "local_ci",
        "production_certified": False,
        "scenarios": {},
    }
report["status"] = "failed"
report["executor_exit_code"] = int(os.environ["EXIT_CODE"])
try:
    with open(os.environ["STARTUP_REPORT_PATH"], encoding="utf-8") as source:
        report["postgres_startup"] = json.load(source)
except (FileNotFoundError, json.JSONDecodeError):
    report["postgres_startup"] = {
        "schema_version": 1,
        "phase": "postgres_startup",
        "status": "failed",
        "code": "startup_evidence_unavailable",
        "calibration_eligible": False,
    }
if not report.get("failure"):
    report["failure"] = "executor_failed_before_complete_capacity_evidence"
with open(path, "w", encoding="utf-8") as destination:
    json.dump(report, destination, indent=2, sort_keys=True)
    destination.write("\n")
'
  {
    echo "# AppSell capacity baseline — 100 admins and 1,000 buyers"
    echo
    echo "- Status: **failed**"
    echo "- Mode: Local/CI"
    echo "- Production certified: **no**"
    echo "- Executor exit code: $exit_code"
  } >"${report%.json}.md"
}

cleanup() {
  local original_status=$?
  if [[ "$cleanup_started" == "true" ]]; then return "$original_status"; fi
  cleanup_started="true"
  trap - EXIT INT TERM
  local down_failed="false"
  if ! docker compose -p "$project" --profile storage down --rmi local --volumes --remove-orphans \
    >/dev/null 2>&1; then
    down_failed="true"
    original_status=1
  fi
  docker image rm "${project}-api:latest" "${project}-pdf-sandbox:latest" \
    >/dev/null 2>&1 || true
  local leftover_containers leftover_networks leftover_volumes leftover_images
  local api_images pdf_images
  if ! leftover_containers="$(docker ps -aq --filter "label=com.docker.compose.project=$project")"; then
    leftover_containers="docker_inspection_failed"
    original_status=1
  fi
  if ! leftover_networks="$(docker network ls -q --filter "label=com.docker.compose.project=$project")"; then
    leftover_networks="docker_inspection_failed"
    original_status=1
  fi
  if ! leftover_volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=$project")"; then
    leftover_volumes="docker_inspection_failed"
    original_status=1
  fi
  if ! api_images="$(docker image ls -q "${project}-api:latest")"; then
    api_images="docker_inspection_failed"
    original_status=1
  fi
  if ! pdf_images="$(docker image ls -q "${project}-pdf-sandbox:latest")"; then
    pdf_images="docker_inspection_failed"
    original_status=1
  fi
  leftover_images="${api_images}${api_images:+$'\n'}${pdf_images}"
  if [[ -n "$leftover_containers$leftover_networks$leftover_volumes$leftover_images" ]]; then
    echo "capacity teardown failed: disposable Docker resources remain" >&2
    original_status=1
  fi
  rm -f -- "$fixture" "$worker_metrics" "$vitest_metrics" || original_status=1
  REPORT_PATH="$report" \
    CONTAINERS_REMAINING="$leftover_containers" \
    NETWORKS_REMAINING="$leftover_networks" \
    VOLUMES_REMAINING="$leftover_volumes" \
    IMAGES_REMAINING="$leftover_images" \
    DOWN_FAILED="$down_failed" \
    python3 -c '
import json, os
path = os.environ["REPORT_PATH"]
try:
    with open(path, encoding="utf-8") as source:
        report = json.load(source)
except (FileNotFoundError, json.JSONDecodeError):
    report = {"schema_version": 2, "mode": "local_ci", "production_certified": False}
report["docker_teardown"] = {
    "containers_remaining": len(os.environ["CONTAINERS_REMAINING"].split()),
    "networks_remaining": len(os.environ["NETWORKS_REMAINING"].split()),
    "volumes_remaining": len(os.environ["VOLUMES_REMAINING"].split()),
    "images_remaining": len(os.environ["IMAGES_REMAINING"].split()),
    "down_succeeded": os.environ["DOWN_FAILED"] == "false",
    "status": "passed" if os.environ["DOWN_FAILED"] == "false" and not any((os.environ["CONTAINERS_REMAINING"], os.environ["NETWORKS_REMAINING"], os.environ["VOLUMES_REMAINING"], os.environ["IMAGES_REMAINING"])) else "failed",
}
with open(path, "w", encoding="utf-8") as destination:
    json.dump(report, destination, indent=2, sort_keys=True)
    destination.write("\n")
' || original_status=1
  if ((original_status != 0)); then mark_report_failed "$original_status"; fi
  if ((original_status == 0)); then
    {
      echo
      echo "## Teardown"
      echo
      echo "Docker containers, networks, volumes and local images remaining: 0."
    } >>"${report%.json}.md"
  fi
  local artifact_paths=()
  for artifact in "$report" "${report%.json}.md" "$startup_report"; do
    if [[ -f "$artifact" ]]; then artifact_paths+=("$artifact"); fi
  done
  if ((${#artifact_paths[@]} == 0)) || \
    ! python3 scripts/assert_capacity_artifact_safe.py "${artifact_paths[@]}"; then
    original_status=1
    mark_report_failed "$original_status"
  fi
  exit "$original_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p "$(dirname "$report")"
rm -f -- "$report" "${report%.json}.md" "$fixture" "$worker_metrics" "$vitest_metrics" "$startup_report"
REPORT_PATH="$report" python3 -c '
import json, os
with open(os.environ["REPORT_PATH"], "w", encoding="utf-8") as destination:
    json.dump({"schema_version": 2, "status": "running", "mode": "local_ci", "production_certified": False}, destination, indent=2, sort_keys=True)
    destination.write("\n")
'

export APPSELL_POSTGRES_PORT="$postgres_port"
export APPSELL_API_PORT="$api_port"
export APPSELL_POSTGRES_SUPERUSER_PASSWORD="$superuser_secret"
export APPSELL_MIGRATOR_PASSWORD="$migrator_secret"
export APPSELL_RUNTIME_PASSWORD="$runtime_secret"
export APPSELL_STORAGE_S3_PORT="$storage_port"
export APPSELL_STORAGE_CONTROL_ACCESS_KEY="$storage_access"
export APPSELL_STORAGE_CONTROL_SECRET_KEY="$storage_secret"
export APPSELL_STORAGE_ALLOWED_ORIGIN="http://localhost:3000"
export APPSELL_STORAGE_BUCKETS="appsell-quarantine,appsell-ready"
export APPSELL_API_ENVIRONMENT="ci"
export APPSELL_API_DATABASE_APPLICATION_NAME="appsell_capacity_api"
export APPSELL_MIGRATOR_DATABASE_URL="postgresql+psycopg://appsell_migrator:${migrator_secret}@127.0.0.1:${postgres_port}/appsell"

if ! docker compose -p "$project" --profile storage up -d --wait --wait-timeout 60 postgres seaweedfs; then
  uv run --project apps/api python scripts/capacity_postgres_startup.py \
    --project "$project" --report "$startup_report" --capture-compose-failure || true
  exit 1
fi
if ! uv run --project apps/api python scripts/capacity_postgres_startup.py \
  --project "$project" --report "$startup_report"; then
  exit 1
fi
docker compose -p "$project" exec -T postgres psql -U postgres -d appsell \
  -v ON_ERROR_STOP=1 \
  -c "comment on database appsell is 'appsell-demo-reset-target-v1'" >/dev/null

uv run --project apps/api alembic -c apps/api/alembic.ini upgrade head

export APPSELL_CONTEXT_SIGNING_KEY_HEX="$context_key"
export APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX="$bootstrap_key"
export APPSELL_BOOTSTRAP_EXPECTED_ISSUER="https://identity.capacity.test.autonomia.site"
export APPSELL_BOOTSTRAP_EXPECTED_AUDIENCE="appsell-capacity-ci"
export APPSELL_BOOTSTRAP_EXPECTED_CLIENT_ID="appsell-capacity-ci"
export APPSELL_BOOTSTRAP_ENABLED="false"
uv run --project apps/api python -m appsell_api.configure_context_key

export APPSELL_TEST_POSTGRES_ADMIN_URL="postgresql://postgres:${superuser_secret}@127.0.0.1:${postgres_port}/appsell"
export APPSELL_TEST_POSTGRES_RUNTIME_URL="postgresql://appsell_runtime:${runtime_secret}@127.0.0.1:${postgres_port}/appsell"
export APPSELL_TEST_CONTEXT_KEY_HEX="$context_key"
export APPSELL_TEST_S3_ENDPOINT="http://127.0.0.1:${storage_port}"
export APPSELL_TEST_S3_ACCESS_KEY="$storage_access"
export APPSELL_TEST_S3_SECRET_KEY="$storage_secret"
uv run --project apps/api pytest -ra apps/api/tests/test_storage_s3_contract.py

export APPSELL_INTERNAL_ISSUER="appsell-bff-capacity"
export APPSELL_INTERNAL_AUDIENCE="appsell-api-capacity"
export APPSELL_IDENTITY_ISSUER="https://identity.capacity.test.autonomia.site"
export APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON="[{\"id\":\"capacity-v1\",\"key\":\"${internal_key_b64}\",\"active\":true}]"
export APPSELL_API_CONTEXT_HMAC_KEY_B64="$(printf '%s' "$context_key" | xxd -r -p | base64)"
export APPSELL_SERVICE_CREDENTIAL_B64="$service_credential_b64"
export APPSELL_API_INTERNAL_ISSUER="$APPSELL_INTERNAL_ISSUER"
export APPSELL_API_INTERNAL_AUDIENCE="$APPSELL_INTERNAL_AUDIENCE"
export APPSELL_API_INTERNAL_HMAC_KEYRING_B64_JSON="$APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON"
export APPSELL_API_SERVICE_CREDENTIAL_B64="$APPSELL_SERVICE_CREDENTIAL_B64"
export APPSELL_API_IDENTITY_ISSUER="$APPSELL_IDENTITY_ISSUER"
export APPSELL_API_STORAGE_PROVIDER="s3"
export APPSELL_API_STORAGE_INTERNAL_ENDPOINT="http://seaweedfs:8333"
export APPSELL_API_STORAGE_PUBLIC_ENDPOINT="http://127.0.0.1:${storage_port}"
export APPSELL_API_STORAGE_REGION="us-east-1"
export APPSELL_API_STORAGE_QUARANTINE_BUCKET="appsell-quarantine"
export APPSELL_API_STORAGE_READY_BUCKET="appsell-ready"
export APPSELL_API_STORAGE_CONTROL_ACCESS_KEY="$storage_access"
export APPSELL_API_STORAGE_CONTROL_SECRET_KEY="$storage_secret"
export APPSELL_API_STORAGE_ALLOWED_ORIGIN="$APPSELL_STORAGE_ALLOWED_ORIGIN"
export APPSELL_API_MALWARE_SCANNER_PROVIDER="deterministic"
export APPSELL_API_PDF_SANDBOX_URL="http://pdf-sandbox.internal:8081/normalize"

if ! docker compose -p "$project" --profile storage up -d --build --wait --wait-timeout 60 api; then
  for service in api pdf-sandbox seaweedfs postgres; do
    container_id="$(docker compose -p "$project" --profile storage ps --all --quiet "$service")"
    if [[ -n "$container_id" ]]; then
      docker inspect --format \
        '{{.Name}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restart_count={{.RestartCount}}' \
        "$container_id" >&2 || true
    fi
  done
  exit 1
fi
uv run --project apps/api --frozen python -m appsell_api.storage_public_audit

# Real S3, worker, sandbox, publication pinning and authorized delivery evidence.
# The host-side worker cannot resolve the Compose-only pdf-sandbox.internal name.
# Its Local/CI isolation path is the subprocess boundary already exercised by
# the canonical storage vertical; the API container keeps its Unix-socket
# sandbox configuration from compose.yaml.
unset APPSELL_API_PDF_SANDBOX_URL
python3 scripts/run_with_rss.py --timeout 120 --output "$worker_metrics" -- \
  uv run --project apps/api pytest -ra apps/api/tests/test_storage_upload_publication_vertical.py

docker compose -p "$project" exec -T postgres psql -U postgres -d appsell \
  -v ON_ERROR_STOP=1 \
  -c "comment on database appsell is 'appsell-capacity-target-v1'" >/dev/null
export APPSELL_CAPACITY_DATABASE_URL="$APPSELL_TEST_POSTGRES_ADMIN_URL"
uv run --project apps/api pytest -q apps/api/tests/test_seed_capacity_tenants.py
uv run --project apps/api python -m appsell_api.seed_capacity_tenants \
  --confirm capacity-100-tenants-v1 --output "$fixture"

postgres_id="$(docker compose -p "$project" ps -q postgres)"
api_id="$(docker compose -p "$project" ps -q api)"
pdf_id="$(docker compose -p "$project" ps -q pdf-sandbox)"
storage_id="$(docker compose -p "$project" ps -q seaweedfs)"
export APPSELL_CAPACITY_CONTAINER_IDS_JSON="$(
  POSTGRES_ID="$postgres_id" API_ID="$api_id" PDF_ID="$pdf_id" STORAGE_ID="$storage_id" \
    python3 -c 'import json,os; print(json.dumps({"postgres":os.environ["POSTGRES_ID"],"api":os.environ["API_ID"],"pdf_sandbox":os.environ["PDF_ID"],"seaweedfs":os.environ["STORAGE_ID"]}))'
)"
export APPSELL_TEST_DOMAIN_API_URL="http://127.0.0.1:${api_port}"
export APPSELL_CAPACITY_MANIFEST="$fixture"
export APPSELL_CAPACITY_REPORT="$report"
export APPSELL_CAPACITY_STARTUP_REPORT="$startup_report"
export APPSELL_CAPACITY_STORAGE_VERTICAL="passed"
export APPSELL_CAPACITY_STORAGE_VERTICAL_TREE_RSS_BYTES="$(
  WORKER_METRICS="$worker_metrics" python3 -c \
    'import json,os; print(json.load(open(os.environ["WORKER_METRICS"], encoding="utf-8"))["peak_rss_bytes"])'
)"
export APPSELL_CAPACITY_OUTER_TIMEOUT_SECONDS="$capacity_executor_timeout_seconds"
export NEXT_TELEMETRY_DISABLED="1"

python3 scripts/run_with_rss.py --timeout "$capacity_executor_timeout_seconds" --output "$vitest_metrics" -- \
  pnpm exec vitest run tests/bff-capacity-100-tenants.test.ts

REPORT_PATH="$report" python3 -c \
  'import json,os; result=json.load(open(os.environ["REPORT_PATH"], encoding="utf-8")); assert result["status"] == "passed" and result["tenant_count"] == 100 and result["admin_accounts"] == 100 and result["buyers_per_admin"] == 10 and result["buyer_accounts"] == 1000 and result["owner_sessions_observed"] == 100 and result["buyer_sessions_observed"] == 1000 and result["distinct_subjects"] == 1100 and result["production_certified"] is False'
python3 scripts/assert_capacity_artifact_safe.py \
  "$report" "${report%.json}.md" "$startup_report"
echo "capacity report: $report"
