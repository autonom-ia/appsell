#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

env -u COMPOSE_PROJECT_NAME -u COMPOSE_FILE -u COMPOSE_PROFILES -u COMPOSE_ENV_FILES \
  docker compose --project-name sell_autonomia_runtime_preview_77 \
  --profile e2e --env-file "$here/runtime-preview.env.example" \
  -f "$here/compose.runtime-preview.yaml" config --format json >"$tmp/config.json"

python3 - "$tmp/config.json" <<'PY'
import json
import pathlib
import sys

config = json.loads(pathlib.Path(sys.argv[1]).read_text())
services = config["services"]
expected = {"domain-bootstrap", "domain-api", "sell-web", "evidence-init", "e2e"}
if set(services) != expected:
    raise SystemExit(f"unexpected runtime-preview services: {sorted(services)}")

network = config["networks"].get("sell-private")
if network is None or network.get("external") is not True:
    raise SystemExit("sell-private must be external")
if network.get("name") != "easypanel-sell-autonomia":
    raise SystemExit("runtime-preview escaped the exclusive sell network")

for name, service in services.items():
    if service.get("pull_policy") != "never":
        raise SystemExit(f"{name} can pull an unverified image")
    image = service.get("image", "")
    if "identity" in image or "postgres" in image or "redis" in image:
        raise SystemExit(f"{name} unexpectedly embeds duplicated infrastructure")
    if name != "sell-web" and service.get("ports"):
        raise SystemExit(f"{name} unexpectedly publishes a host port")

ports = services["sell-web"].get("ports", [])
if len(ports) != 1 or ports[0].get("host_ip") != "127.0.0.1":
    raise SystemExit("sell-web must publish exactly one loopback-only port")
if ports[0].get("published") != "13200" or ports[0].get("target") != 13200:
    raise SystemExit("sell-web loopback port drifted")

if services["domain-api"].get("networks") != {"sell-private": None}:
    raise SystemExit("domain-api must use only the exclusive sell network")
if services["sell-web"].get("networks") != {"sell-private": None}:
    raise SystemExit("sell-web must start only on the exclusive sell network")
if services["sell-web"].get("environment", {}).get("APPSELL_REDIS_PREFIX") != (
    "appsell:runtime-preview:issue77"
):
    raise SystemExit("sell-web Redis prefix must satisfy the OAuth namespace contract")
if services["e2e"].get("network_mode") != "service:sell-web":
    raise SystemExit("E2E must share the loopback namespace of sell-web")
evidence_init = services["evidence-init"].get("command")
if not isinstance(evidence_init, list) or evidence_init[:2] != ["sh", "-ec"]:
    raise SystemExit("evidence-init command shell drifted")
evidence_init_script = evidence_init[2] if len(evidence_init) == 3 else ""
for required_guard in (
    '700:1001:1001) ;;',
    '755:0:0|700:0:0)',
    'chmod 0700 /evidence',
    'chown 1001:1001 /evidence',
    'unexpected evidence volume state',
    'test "$(stat -c \'%a:%u:%g\' /evidence)" = "700:1001:1001"',
):
    if required_guard not in evidence_init_script:
        raise SystemExit(f"evidence-init state guard drifted: {required_guard}")
if evidence_init_script.index("chmod 0700 /evidence") > evidence_init_script.index(
    "chown 1001:1001 /evidence"
):
    raise SystemExit("evidence-init must set mode before dropping volume ownership")
if services["evidence-init"].get("cap_add") != ["CHOWN"]:
    raise SystemExit("evidence-init must not require FOWNER or broader capabilities")
if services["e2e"].get("environment") != {
    "E2E_RUNTIME_ORIGIN": "http://localhost:13200",
    "E2E_RUNTIME_EVIDENCE_DIR": "/evidence/staged",
}:
    raise SystemExit("E2E received application secrets or unexpected environment")
bootstrap_environment = services["domain-bootstrap"].get("environment", {})
for key, expected_value in {
    "NODE_ENV": "test",
    "APP_ENV": "ci",
    "APPSELL_DEMO_REMOTE_RESET_SCOPE": "appsell-runtime-preview-77",
    "APPSELL_POSTGRES_HOST": "sell-autonomia_postgres",
    "APPSELL_POSTGRES_PORT": "5432",
    "APPSELL_DATABASE_NAME": "appsell_preview_77",
}.items():
    if bootstrap_environment.get(key) != expected_value:
        raise SystemExit(f"domain-bootstrap {key} escaped the exact preview target")
for service_name in ("domain-api", "sell-web", "evidence-init", "e2e"):
    if "APPSELL_DEMO_REMOTE_RESET_SCOPE" in services[service_name].get("environment", {}):
        raise SystemExit("remote demo reset scope leaked beyond the one-shot bootstrap")

rendered = pathlib.Path(sys.argv[1]).read_text().lower()
for forbidden in ("dados", "flow", "identity-postgres", "domain-postgres"):
    if forbidden in rendered:
        raise SystemExit(f"runtime-preview references forbidden scope: {forbidden}")
if "appsell_preview_77" not in rendered:
    raise SystemExit("runtime-preview does not pin its additive database")
PY

grep -Fq 'APPSELL_DATABASE_NAME: appsell_preview_77' "$here/compose.runtime-preview.yaml"
grep -Fq '127.0.0.1:13200:13200' "$here/compose.runtime-preview.yaml"
grep -Fq 'network_mode: "service:sell-web"' "$here/compose.runtime-preview.yaml"
grep -Fq 'APPSELL_OAUTH_MODE: local' "$here/compose.runtime-preview.yaml"
grep -Fq 'state="$$(stat -c '\''%a:%u:%g'\'' /evidence)"' \
  "$here/compose.runtime-preview.yaml"
grep -Fq 'case "$$state" in' "$here/compose.runtime-preview.yaml"
grep -Fq 'test "$$(stat -c '\''%a:%u:%g'\'' /evidence)" = "700:1001:1001"' \
  "$here/compose.runtime-preview.yaml"
grep -Fq 'test "$(id -u pwuser):$(id -g pwuser)" = "1001:1001"' \
  "$here/../preview-vps/Dockerfile.sell"
grep -Fq 'USER pwuser' "$here/../preview-vps/Dockerfile.sell"
grep -Fq -- '--entrypoint sh e2e -ec' "$here/verify-runtime-preview.sh"
grep -Fq 'unset PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER PGPASSWORD PGPASSFILE' \
  "$here/bootstrap-runtime-preview.sh"
grep -Fq 'unset PGSERVICE PGSERVICEFILE PGSYSCONFDIR PGOPTIONS PGAPPNAME' \
  "$here/bootstrap-runtime-preview.sh"
for marker_script in bootstrap-runtime-preview.sh retire-runtime-preview-credentials.sh; do
  grep -Fq "shobj_description(oid,'pg_database')" "$here/$marker_script"
  if grep -Eq "(^|[^[:alnum:]_])obj_description\\(oid,'pg_database'\\)" \
    "$here/$marker_script"; then
    echo "runtime-preview database marker must use the shared-object comment catalog" >&2
    exit 1
  fi
done
if grep -Eq '(^|[^a-z])(down|rm|prune|drop database)([^a-z]|$)' \
  "$here/bootstrap-runtime-preview.sh"; then
  echo "runtime bootstrap contains a destructive operation" >&2
  exit 1
fi
for script in load-runtime-preview-images.sh preflight-runtime-preview.sh \
  remove-runtime-preview-images.sh up-runtime-preview.sh verify-runtime-preview.sh \
  teardown-runtime-preview.sh retire-runtime-preview-credentials.sh test-runtime-preview-common.sh \
  test-runtime-preview-lifecycle.sh; do
  test -x "$here/$script"
done
grep -Fq 'RUNTIME_PREVIEW_PROJECT="sell-autonomia"' "$here/runtime-preview-common.sh"
grep -Fq 'RUNTIME_PREVIEW_SERVICE="runtime-preview"' "$here/runtime-preview-common.sh"
grep -Fq 'http://127.0.0.1:3000' "$here/runtime-preview-common.sh"
grep -Fq 'CONFIRM_RUNTIME_PREVIEW_UP=appsell-runtime-preview-77' "$here/up-runtime-preview.sh"
grep -Fq 'CONFIRM_RUNTIME_PREVIEW_TEARDOWN=appsell-runtime-preview-77-teardown' "$here/teardown-runtime-preview.sh"
grep -Fq 'createDotEnv:false' "$here/up-runtime-preview.sh"
grep -Fq 'env:"",createDotEnv:false' "$here/up-runtime-preview.sh"
grep -Fq 'down --remove-orphans' "$here/runtime-preview-common.sh"
if grep -Fq 'destroyService' "$here/up-runtime-preview.sh" "$here/teardown-runtime-preview.sh"; then
  echo "runtime-preview lifecycle still depends on broken EasyPanel destroyService" >&2
  exit 1
fi
if grep -Fq 'createDotEnv:true' "$here/up-runtime-preview.sh"; then
  echo "runtime-preview would create a temporarily world-readable EasyPanel env" >&2
  exit 1
fi
if grep -Eq 'down[^\n]*(--volumes|-v)( |$)' "$here/runtime-preview-common.sh"; then
  echo "runtime-preview teardown would remove persistent volumes" >&2
  exit 1
fi
if grep -En 'docker\.(inspect|ps|image|service)' "$here"/*runtime-preview*.sh; then
  echo "runtime-preview scripts contain a malformed docker command" >&2
  exit 1
fi

echo "EasyPanel runtime-preview contract: passed"
