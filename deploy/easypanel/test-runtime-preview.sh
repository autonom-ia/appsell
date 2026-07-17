#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

env -u COMPOSE_PROJECT_NAME -u COMPOSE_FILE -u COMPOSE_PROFILES -u COMPOSE_ENV_FILES \
  docker compose --project-name sell_autonomia_runtime_preview_77 \
  --profile e2e --env-file "$here/runtime-preview.env.example" \
  -f "$here/compose.runtime-preview.yaml" config --format json >"$tmp/config.json"

python3 - "$tmp/config.json" "$here/verify-runtime-preview.sh" \
  "$here/runtime-preview-common.sh" "$here/up-runtime-preview.sh" \
  "$here/../../playwright.runtime-preview.config.ts" <<'PY'
import json
import pathlib
import re
import sys

config = json.loads(pathlib.Path(sys.argv[1]).read_text())
services = config["services"]
expected = {
    "domain-bootstrap",
    "domain-api",
    "pdf-sandbox-init",
    "pdf-sandbox",
    "sell-web",
    "ingress-proxy",
    "evidence-init",
    "e2e-transport-init",
    "e2e-bridge",
    "e2e",
}
if set(services) != expected:
    raise SystemExit(f"unexpected runtime-preview services: {sorted(services)}")

networks = config["networks"]
network = networks.get("sell-private")
if network is None or network.get("external") is not True:
    raise SystemExit("sell-private must be external")
if network.get("name") != "easypanel-sell-autonomia":
    raise SystemExit("runtime-preview escaped the exclusive sell network")
ingress_network = networks.get("sell-ingress")
if ingress_network is None or ingress_network.get("internal") is not True:
    raise SystemExit("sell-ingress must be an internal project-owned network")
if ingress_network.get("external") is True:
    raise SystemExit("sell-ingress must be removed with the Compose project")
if ingress_network.get("name") != "sell-autonomia-runtime-preview-ingress-77":
    raise SystemExit("sell-ingress escaped the exact runtime-preview name")
if ingress_network.get("labels") != {
    "com.autonomia.environment": "local-ci-easypanel-preview",
    "com.autonomia.issue": "77",
    "com.autonomia.project": "sell-autonomia",
    "com.autonomia.purpose": "ingress-proxy",
    "com.autonomia.source-sha": "0123456789abcdef0123456789abcdef01234567",
}:
    raise SystemExit("sell-ingress ownership labels drifted")
easypanel_ingress = networks.get("easypanel-ingress")
if easypanel_ingress is None or easypanel_ingress.get("external") is not True:
    raise SystemExit("easypanel-ingress must reuse the existing EasyPanel ingress network")
if easypanel_ingress.get("name") != "easypanel":
    raise SystemExit("easypanel-ingress drifted from the Traefik network")
e2e_network = networks.get("sell-public-e2e")
if e2e_network is None or e2e_network.get("internal") is True:
    raise SystemExit("sell-public-e2e must permit only the bridge to reach the public endpoint")
if e2e_network.get("external") is True:
    raise SystemExit("sell-public-e2e must be owned by this Compose project")
if e2e_network.get("name") != "sell_autonomia_runtime_preview_77_sell-public-e2e":
    raise SystemExit("sell-public-e2e escaped the exact test Compose project")

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

for service_name in ("domain-bootstrap", "domain-api"):
    if services[service_name].get("networks") != {"sell-private": None}:
        raise SystemExit(f"{service_name} must use only the exclusive sell network")
for service_name in ("pdf-sandbox-init", "pdf-sandbox"):
    if services[service_name].get("network_mode") != "none" or services[service_name].get("networks"):
        raise SystemExit(f"{service_name} must have no IP network")
pdf_volume = {
    "type": "volume",
    "source": "runtime_pdf_socket",
    "target": "/run/appsell-pdf",
}
init_mounts = services["pdf-sandbox-init"].get("volumes", [])
sandbox_mounts = services["pdf-sandbox"].get("volumes", [])
if init_mounts != [pdf_volume] or sandbox_mounts != [pdf_volume]:
    raise SystemExit("PDF sandbox services must share only the dedicated socket volume")
if services["pdf-sandbox-init"].get("user") != "0:0":
    raise SystemExit("PDF sandbox init must use its narrowly-scoped root setup user")
if services["pdf-sandbox-init"].get("cap_add") != ["CHOWN"]:
    raise SystemExit("PDF sandbox init must receive only CHOWN")
if services["pdf-sandbox"].get("image") != services["domain-api"].get("image"):
    raise SystemExit("PDF sandbox must use the attested domain image")
if services["pdf-sandbox"].get("user") != "10001:10001":
    raise SystemExit("PDF sandbox must run as the dedicated numeric user")
if services["pdf-sandbox"].get("environment") != {
    "APPSELL_PDF_SANDBOX_RECYCLE_AFTER_JOB": "true"
}:
    raise SystemExit("PDF sandbox must recycle its whole container after every media job")
if services["pdf-sandbox"].get("cap_add"):
    raise SystemExit("PDF sandbox must not add capabilities")
if services["pdf-sandbox"].get("ports"):
    raise SystemExit("PDF sandbox must not publish ports")
if "/workspace/sell/output/pdf:size=1048576,mode=000,noexec,nosuid" not in services[
    "pdf-sandbox"
].get("tmpfs", []):
    raise SystemExit("PDF sandbox must not see PDFs embedded in the domain image")
if services["pdf-sandbox"].get("depends_on") != {
    "pdf-sandbox-init": {"condition": "service_completed_successfully", "required": True}
}:
    raise SystemExit("PDF sandbox socket ownership init dependency drifted")
domain_mounts = services["domain-api"].get("volumes", [])
if domain_mounts != [{**pdf_volume, "read_only": True}]:
    raise SystemExit("Domain API must mount the dedicated PDF socket read-only")
init_command = services["pdf-sandbox-init"].get("command", [])
init_script = init_command[2] if len(init_command) == 3 else ""
for guard in (
    "755:0:0|700:0:0)",
    "700:10001:10001)",
    "chmod 0700 /run/appsell-pdf",
    "chown 10001:10001 /run/appsell-pdf",
    'test "$$(stat -c \'%a:%u:%g\' /run/appsell-pdf)" = "700:10001:10001"',
):
    if guard not in init_script:
        raise SystemExit(f"PDF socket init state guard drifted: {guard}")
if services["sell-web"].get("networks") != {
    "sell-ingress": {"aliases": ["sell-web-ingress-77"]},
    "sell-private": None,
}:
    raise SystemExit("sell-web must use only the private and dedicated ingress networks")
if services["ingress-proxy"].get("networks") != {
    "easypanel-ingress": {"aliases": ["sell-autonomia_runtime-preview_sell-web"]},
    "sell-ingress": None,
}:
    raise SystemExit("ingress proxy networks or Traefik alias drifted")
if services["ingress-proxy"].get("environment"):
    raise SystemExit("ingress proxy must not receive application environment variables")
if services["ingress-proxy"].get("user") != "10001:10001":
    raise SystemExit("ingress proxy user drifted from the rootless domain image")
proxy_command = services["ingress-proxy"].get("command", [])
expected_proxy_script = """import asyncio

async def pump(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()

async def handle(client_reader, client_writer):
    server_reader, server_writer = await asyncio.open_connection("sell-web-ingress-77", 13200)
    await asyncio.gather(
        pump(client_reader, server_writer),
        pump(server_reader, client_writer),
    )

async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", 13200)
    async with server:
        await server.serve_forever()

asyncio.run(main())
"""
if proxy_command != ["python", "-c", expected_proxy_script]:
    raise SystemExit("ingress proxy command drifted")
proxy = services["ingress-proxy"]
if proxy.get("image") != "autonomia-sell-preview-domain:0123456789abcdef0123456789abcdef01234567":
    raise SystemExit("ingress proxy image drifted from the attested domain runtime")
if proxy.get("image") != services["domain-api"].get("image"):
    raise SystemExit("ingress proxy must share the exact domain-api image reference")
if proxy.get("labels") != {
    "com.autonomia.environment": "local-ci-easypanel-preview",
    "com.autonomia.issue": "77",
    "com.autonomia.project": "sell-autonomia",
    "com.autonomia.purpose": "ingress-proxy",
    "com.autonomia.source-sha": "0123456789abcdef0123456789abcdef01234567",
}:
    raise SystemExit("ingress proxy ownership labels drifted")
if proxy.get("read_only") is not True or proxy.get("cap_drop") != ["ALL"]:
    raise SystemExit("ingress proxy root filesystem or capabilities drifted")
if proxy.get("security_opt") != ["no-new-privileges:true"]:
    raise SystemExit("ingress proxy no-new-privileges guard drifted")
if proxy.get("restart") != "unless-stopped" or proxy.get("pids_limit") != 64:
    raise SystemExit("ingress proxy lifecycle limits drifted")
if proxy.get("mem_limit") != "67108864" or proxy.get("cpus") != 0.1:
    raise SystemExit("ingress proxy resource limits drifted")
if proxy.get("logging") != {
    "driver": "json-file",
    "options": {"max-file": "3", "max-size": "10m"},
}:
    raise SystemExit("ingress proxy logging limits drifted")
if proxy.get("ports") or proxy.get("volumes"):
    raise SystemExit("ingress proxy must not publish ports or mount storage")
if proxy.get("depends_on") != {"sell-web": {"condition": "service_healthy", "required": True}}:
    raise SystemExit("ingress proxy dependency drifted")
if services["sell-web"].get("command") != [
    "./node_modules/.bin/next", "start", "--hostname", "0.0.0.0", "-p", "13200"
]:
    raise SystemExit("sell-web must run the built Next production server")
if services["sell-web"].get("environment", {}).get("NODE_ENV") != "production":
    raise SystemExit("public sell-web must use production runtime semantics")
if services["sell-web"].get("environment", {}).get("APPSELL_PUBLIC_DEMO_RUNTIME") != (
    "sell-autonomia-runtime-preview-77"
):
    raise SystemExit("public demo runtime marker drifted")
if services["sell-web"].get("environment", {}).get("APPSELL_REDIS_PREFIX") != (
    "appsell:runtime-preview:issue77"
):
    raise SystemExit("sell-web Redis prefix must satisfy the OAuth namespace contract")
if services["sell-web"].get("environment", {}).get("APPSELL_PUBLIC_ORIGIN") != (
    "https://sell.autonomia.site"
):
    raise SystemExit("sell-web public origin must use the approved HTTPS domain")
if services["e2e-bridge"].get("networks") != {"sell-public-e2e": None}:
    raise SystemExit("E2E bridge must use only its dedicated public-test network")
if services["e2e"].get("network_mode") != "none" or services["e2e"].get("networks"):
    raise SystemExit("E2E must have no IP network")
if services["e2e"].get("cap_add") != ["NET_BIND_SERVICE"]:
    raise SystemExit("E2E must receive only the capability required for its loopback TLS listener")
if services["e2e"].get("extra_hosts") != ["sell.autonomia.site=127.0.0.1"]:
    raise SystemExit("E2E must resolve only the approved public host through its loopback proxy")
e2e_members = {
    name for name, service in services.items() if "sell-public-e2e" in service.get("networks", {})
}
if e2e_members != {"e2e-bridge"}:
    raise SystemExit(f"unexpected services on sell-public-e2e: {sorted(e2e_members)}")
private_members = {
    name for name, service in services.items() if "sell-private" in service.get("networks", {})
}
if private_members != {"domain-bootstrap", "domain-api", "sell-web"}:
    raise SystemExit(f"unexpected services on sell-private: {sorted(private_members)}")
ingress_members = {
    name for name, service in services.items() if "sell-ingress" in service.get("networks", {})
}
if ingress_members != {"sell-web", "ingress-proxy"}:
    raise SystemExit(f"unexpected services on sell-ingress: {sorted(ingress_members)}")
easypanel_members = {
    name for name, service in services.items() if "easypanel-ingress" in service.get("networks", {})
}
if easypanel_members != {"ingress-proxy"}:
    raise SystemExit(f"unexpected services on easypanel ingress: {sorted(easypanel_members)}")
volumes = config["volumes"]
for volume_name, purpose in {
    "runtime_pdf_socket": "pdf-raster-socket",
    "runtime_evidence": "runtime-e2e-evidence",
    "runtime_e2e_transport": "runtime-e2e-transport",
}.items():
    labels = volumes[volume_name].get("labels", {})
    expected_labels = {
        "com.autonomia.project": "sell-autonomia",
        "com.autonomia.issue": "77",
        "com.autonomia.environment": "local-ci-easypanel-preview",
        "com.autonomia.purpose": purpose,
    }
    if labels != expected_labels or "com.autonomia.source-sha" in labels:
        raise SystemExit(f"{volume_name} ownership labels drifted")
evidence_init = services["evidence-init"].get("command")
if not isinstance(evidence_init, list) or evidence_init[:2] != ["sh", "-ec"]:
    raise SystemExit("evidence-init command shell drifted")
evidence_init_script = evidence_init[2] if len(evidence_init) == 3 else ""
for required_guard in (
    'state="$$(stat -c \'%a:%u:%g\' /evidence)"',
    'case "$$state" in',
    '700:1001:1001) ;;',
    '755:0:0|700:0:0)',
    'chmod 0700 /evidence',
    'chown 1001:1001 /evidence',
    'unexpected evidence volume state',
    'test "$$(stat -c \'%a:%u:%g\' /evidence)" = "700:1001:1001"',
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
    "E2E_RUNTIME_BRIDGE_SOCKET": "/transport/sell.sock",
    "E2E_RUNTIME_ORIGIN": "https://sell.autonomia.site",
    "E2E_RUNTIME_EVIDENCE_DIR": "/evidence/staged",
}:
    raise SystemExit("E2E received application secrets or unexpected environment")
if services["e2e-bridge"].get("environment") != {
    "E2E_RUNTIME_BRIDGE_SOCKET": "/transport/sell.sock"
}:
    raise SystemExit("E2E bridge received application secrets or unexpected environment")
if services["e2e-bridge"].get("ports"):
    raise SystemExit("E2E bridge must not publish a port")
transport_init = services["e2e-transport-init"]
if transport_init.get("network_mode") != "none" or transport_init.get("networks"):
    raise SystemExit("E2E transport init must have no IP network")
transport_init_command = transport_init.get("command")
if not isinstance(transport_init_command, list) or transport_init_command[:2] != ["sh", "-ec"]:
    raise SystemExit("E2E transport init command shell drifted")
transport_init_script = transport_init_command[2] if len(transport_init_command) == 3 else ""
for required_guard in (
    'test -L /transport/sell.sock',
    'test -S /transport/sell.sock',
    '600:1001:1001',
    'rm -f /transport/sell.sock',
    'find /transport -mindepth 1 -maxdepth 1',
):
    if required_guard not in transport_init_script:
        raise SystemExit(f"E2E transport init guard drifted: {required_guard}")
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
for service_name in (
    "domain-api",
    "pdf-sandbox-init",
    "pdf-sandbox",
    "sell-web",
    "ingress-proxy",
    "evidence-init",
    "e2e-transport-init",
    "e2e-bridge",
    "e2e",
):
    if "APPSELL_DEMO_REMOTE_RESET_SCOPE" in services[service_name].get("environment", {}):
        raise SystemExit("remote demo reset scope leaked beyond the one-shot bootstrap")

rendered = pathlib.Path(sys.argv[1]).read_text().lower()
for forbidden in ("dados", "flow", "identity-postgres", "domain-postgres"):
    if forbidden in rendered:
        raise SystemExit(f"runtime-preview references forbidden scope: {forbidden}")
if "appsell_preview_77" not in rendered:
    raise SystemExit("runtime-preview does not pin its additive database")

verifier = pathlib.Path(sys.argv[2]).read_text()
for required_verifier_guard in (
    '.[0].Config.Labels["org.opencontainers.image.revision"] == $source_sha',
    'domain_image_env_json="$(jq -ce',
    '.[0].Config.Env == $domain_image_env',
    '.[0].Image == $domain_image_id',
    '.[0].Config.Cmd == $expected_cmd',
    '.[0].Config.Entrypoint == null',
    '.[0].Config.User == "10001:10001"',
    '.[0].Config.Labels["com.autonomia.purpose"] == "ingress-proxy"',
    '.[0].HostConfig.RestartPolicy == {"Name":"unless-stopped","MaximumRetryCount":0}',
    '.[0].HostConfig.Memory == 67108864',
    '.[0].HostConfig.NanoCpus == 100000000',
    '.[0].HostConfig.PidsLimit == 64',
    '.[0].HostConfig.LogConfig == {"Type":"json-file","Config":{"max-file":"3","max-size":"10m"}}',
    'runtime_preview_verify_easypanel_ingress_network_json "$global_network_json" "$ingress_id"',
    'EasyPanel runtime-preview ingress alias must appear exactly once',
):
    if required_verifier_guard not in verifier:
        raise SystemExit(f"runtime verifier guard drifted: {required_verifier_guard}")
if 'select(test("APPSELL|PASSWORD|SECRET|TOKEN|KEY|REDIS|DATABASE"))' in verifier:
    raise SystemExit("runtime verifier still rejects inherited image environment by variable name")

common = pathlib.Path(sys.argv[3]).read_text()
for required_common_guard in (
    'runtime_preview_acquire_transition_lock()',
    'runtime_preview_verify_easypanel_ingress_network_json()',
    '.[0].Driver == "overlay"',
    '.[0].Scope == "swarm"',
    '.[0].Attachable == true',
    '.[0].Internal == false',
    'test("^easypanel-traefik\\\\.1\\\\.")',
    '. == "lb-easypanel"',
    'test("^[0-9a-f]{64}$")',
):
    if required_common_guard not in common:
        raise SystemExit(f"runtime common guard drifted: {required_common_guard}")

up_script = pathlib.Path(sys.argv[4]).read_text()
if up_script.index("runtime_preview_acquire_transition_lock") > up_script.index(
    '"$here/preflight-runtime-preview.sh"'
):
    raise SystemExit("runtime-preview up must acquire the transition lock before preflight")

playwright_config = pathlib.Path(sys.argv[5]).read_text()
resolver_args = re.findall(r'["\'](--host-resolver-rules=[^"\']+)["\']', playwright_config)
expected_resolver_arg = "--host-resolver-rules=MAP sell.autonomia.site 127.0.0.1"
if resolver_args != [expected_resolver_arg]:
    raise SystemExit(f"runtime-preview Chromium resolver args drifted: {resolver_args}")
if "*" in resolver_args[0] or "85.31.60.100" in resolver_args[0]:
    raise SystemExit("runtime-preview Chromium resolver must not use a wildcard or public IP")
device_spread = '...devices["iPhone 15"]'
if playwright_config.count(device_spread) != 1:
    raise SystemExit("runtime-preview must inherit the iPhone 15 device exactly once")
browser_names = re.findall(r'\bbrowserName\s*:\s*["\']([^"\']+)["\']', playwright_config)
if browser_names != ["chromium"]:
    raise SystemExit(f"runtime-preview browser target drifted: {browser_names}")
if playwright_config.index('browserName: "chromium"') < playwright_config.index(device_spread):
    raise SystemExit("runtime-preview Chromium override must follow the iPhone device spread")
if re.search(r'\b(browserName|defaultBrowserType)\s*:\s*["\']webkit["\']', playwright_config):
    raise SystemExit("runtime-preview must not resolve its launch target to WebKit")
PY

domain_image_fixture='[{"Config":{"Env":["PATH=/usr/local/bin:/usr/bin","GPG_KEY=public-image-key"]}}]'
domain_image_env_fixture="$(jq -ce '
  .[0].Config.Env |
  if . == null then .
  elif type == "array" and all(.[]; type == "string") then .
  else error("domain runtime image environment is invalid")
  end
' <<<"$domain_image_fixture")"
matching_container_fixture='[{"Config":{"Env":["PATH=/usr/local/bin:/usr/bin","GPG_KEY=public-image-key"]}}]'
jq -e --argjson domain_image_env "$domain_image_env_fixture" '
  type == "array" and length == 1 and .[0].Config.Env == $domain_image_env
' <<<"$matching_container_fixture" >/dev/null

for rejected_container_fixture in \
  '[{"Config":{"Env":["PATH=/usr/local/bin:/usr/bin","GPG_KEY=public-image-key","APPSELL_SECRET=unexpected"]}}]' \
  '[{"Config":{"Env":["PATH=/usr/local/bin:/usr/bin","GPG_KEY=public-image-key","EXTRA_RUNTIME=value"]}}]' \
  '[{"Config":{"Env":["PATH=/usr/local/bin:/usr/bin","GPG_KEY=drifted"]}}]'
do
  if jq -e --argjson domain_image_env "$domain_image_env_fixture" '
    type == "array" and length == 1 and .[0].Config.Env == $domain_image_env
  ' <<<"$rejected_container_fixture" >/dev/null; then
    echo "runtime verifier environment fixture accepted container drift" >&2
    exit 1
  fi
done

grep -Fq 'APPSELL_DATABASE_NAME: appsell_preview_77' "$here/compose.runtime-preview.yaml"
grep -Fq '127.0.0.1:13200:13200' "$here/compose.runtime-preview.yaml"
if grep -Fq 'network_mode: "service:sell-web"' "$here/compose.runtime-preview.yaml"; then
  echo "runtime-preview E2E still shares the sell-web network namespace" >&2
  exit 1
fi
grep -Fq 'APPSELL_OAUTH_MODE: local' "$here/compose.runtime-preview.yaml"
grep -Fq 'state="$$(stat -c '\''%a:%u:%g'\'' /evidence)"' \
  "$here/compose.runtime-preview.yaml"
grep -Fq 'case "$$state" in' "$here/compose.runtime-preview.yaml"
grep -Fq 'test "$$(stat -c '\''%a:%u:%g'\'' /evidence)" = "700:1001:1001"' \
  "$here/compose.runtime-preview.yaml"
grep -Fq 'test "$(id -u pwuser):$(id -g pwuser)" = "1001:1001"' \
  "$here/../preview-vps/Dockerfile.sell"
grep -Fq 'USER pwuser' "$here/../preview-vps/Dockerfile.sell"
grep -Fq 'corepack pnpm build' "$here/../preview-vps/Dockerfile.sell"
grep -Fq '__Host-appsell_session' "$here/../../tests/e2e-runtime-preview/demo-personas.spec.ts"
grep -Fq 'const secureRuntime = runtimeOrigin.protocol === "https:"' \
  "$here/../../tests/e2e-runtime-preview/demo-personas.spec.ts"
grep -Fq 'secure: secureRuntime' "$here/../../tests/e2e-runtime-preview/demo-personas.spec.ts"
grep -Fq -- '--entrypoint sh e2e -ec' "$here/verify-runtime-preview.sh"
grep -Fq -- '--rm --no-deps e2e' "$here/verify-runtime-preview.sh"
grep -Fq 'runtime-preview remained healthy with stable IDs and zero restarts for 30 seconds after E2E' \
  "$here/verify-runtime-preview.sh"
grep -Fq 'runtime-preview-loopback-proxy.mjs' "$here/run-runtime-preview-e2e.sh"
grep -Fq 'upstreamSocket: "/transport/sell.sock"' "$here/runtime-preview-loopback-proxy.mjs"
grep -Fq 'upstreamHost: "85.31.60.100"' "$here/runtime-preview-unix-bridge.mjs"
grep -Fq 'upstreamPort: 443' "$here/runtime-preview-unix-bridge.mjs"
grep -Fq 'server.listen({ host, port: listenPort, ipv6Only: host === "::1" }' \
  "$here/runtime-preview-loopback-proxy.mjs"
node --check "$here/runtime-preview-loopback-proxy.mjs"
node --check "$here/test-runtime-preview-loopback-proxy.mjs"
node --check "$here/runtime-preview-unix-bridge.mjs"
node --check "$here/test-runtime-preview-unix-bridge.mjs"
node --test "$here/test-runtime-preview-loopback-proxy.mjs"
node --test "$here/test-runtime-preview-unix-bridge.mjs"
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
for script in activate-public-runtime-preview.sh deactivate-public-runtime-preview.sh \
  load-runtime-preview-images.sh migrate-legacy-runtime-evidence.sh preflight-runtime-preview.sh \
  remove-runtime-preview-images.sh up-runtime-preview.sh verify-runtime-preview.sh \
  teardown-runtime-preview.sh retire-runtime-preview-credentials.sh test-runtime-preview-common.sh \
  test-runtime-preview-domain.sh test-runtime-preview-evidence-migration.sh \
  test-runtime-preview-lifecycle.sh; do
  test -x "$here/$script"
done
grep -Fq 'RUNTIME_PREVIEW_PROJECT="sell-autonomia"' "$here/runtime-preview-common.sh"
grep -Fq 'RUNTIME_PREVIEW_SERVICE="runtime-preview"' "$here/runtime-preview-common.sh"
grep -Fq 'RUNTIME_PREVIEW_E2E_NETWORK="${RUNTIME_PREVIEW_COMPOSE_PROJECT}_sell-public-e2e"' \
  "$here/runtime-preview-common.sh"
grep -Fq 'RUNTIME_PREVIEW_PDF_SOCKET_VOLUME="${RUNTIME_PREVIEW_COMPOSE_PROJECT}_runtime_pdf_socket"' \
  "$here/runtime-preview-common.sh"
grep -Fq 'RUNTIME_PREVIEW_EVIDENCE_VOLUME="${RUNTIME_PREVIEW_COMPOSE_PROJECT}_runtime_evidence"' \
  "$here/runtime-preview-common.sh"
grep -Fq 'RUNTIME_PREVIEW_E2E_TRANSPORT_VOLUME="${RUNTIME_PREVIEW_COMPOSE_PROJECT}_runtime_e2e_transport"' \
  "$here/runtime-preview-common.sh"
grep -Fq 'runtime_preview_lifecycle_fingerprint_from_inspect' \
  "$here/verify-runtime-preview.sh"
grep -Fq 'runtime_preview_verify_public_attestation' "$here/verify-runtime-preview.sh"
grep -Fq 'runtime_preview_ensure_owned_volume' "$here/verify-runtime-preview.sh"
grep -Fq 'runtime_pdf_socket pdf-raster-socket' "$here/up-runtime-preview.sh"
grep -Fq 'appsell_pdf_worker_planned_recycle' "$here/verify-runtime-preview.sh"
grep -Fq 'os._exit(77)' "$here/../../apps/api/src/appsell_api/pdf_sandbox_service.py"
grep -Fq 'runtime_preview_delete_exact_public_domain' "$here/activate-public-runtime-preview.sh"
grep -Fq 'CONFIRM_RUNTIME_PREVIEW_PUBLIC_RETRY=sell-autonomia-public-retry-77' \
  "$here/activate-public-runtime-preview.sh"
grep -Fq '{json:{id:"",certificateResolver:""' \
  "$here/activate-public-runtime-preview.sh"
grep -Fq 'runtime_preview_acquire_transition_lock "$RUNTIME_PREVIEW_TRANSITION_LOCK"' \
  "$here/activate-public-runtime-preview.sh"
grep -Fq 'runtime_preview_acquire_transition_lock "$RUNTIME_PREVIEW_TRANSITION_LOCK"' \
  "$here/up-runtime-preview.sh"
grep -Fq 'runtime_preview_acquire_transition_lock "$RUNTIME_PREVIEW_TRANSITION_LOCK"' \
  "$here/deactivate-public-runtime-preview.sh"
grep -Fq 'runtime_preview_acquire_transition_lock "$RUNTIME_PREVIEW_TRANSITION_LOCK"' \
  "$here/teardown-runtime-preview.sh"
grep -Fq 'runtime-preview ingress alias is already occupied' "$here/preflight-runtime-preview.sh"
grep -Fq 'owned_ingress_ids' "$here/runtime-preview-common.sh"
grep -Fq '/api/rpc/domains/deleteDomain' "$here/runtime-preview-common.sh"
grep -Fq 'strict-transport-security:' "$here/activate-public-runtime-preview.sh"
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
