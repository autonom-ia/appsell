#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
install -d "$tmp/bin"

cat >"$tmp/bin/docker" <<'DOCKER'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1 $2" == "service inspect" ]] || exit 2
case " $* " in
  *" sell-autonomia_postgres "*)
    printf '%s\n' 'POSTGRES_USER=postgres' 'POSTGRES_PASSWORD=exclusive-postgres-fixture' \
      'POSTGRES_DB=appsell'
    ;;
  *" sell-autonomia_redis "*)
    printf '%s\n' 'REDIS_PASSWORD=exclusive-redis-fixture'
    ;;
  *) exit 2 ;;
esac
DOCKER
chmod 0700 "$tmp/bin/docker"

PATH="$tmp/bin:$PATH" "$here/generate-runtime-preview-env.sh" \
  "$tmp/generated" 0123456789abcdef0123456789abcdef01234567 >"$tmp/log"

directory_mode="$(stat -c '%a' "$tmp/generated" 2>/dev/null || stat -f '%Lp' "$tmp/generated")"
file_mode="$(stat -c '%a' "$tmp/generated/runtime-preview.env" 2>/dev/null \
  || stat -f '%Lp' "$tmp/generated/runtime-preview.env")"
[[ "$directory_mode" == "700" && "$file_mode" == "600" ]] || {
  echo "runtime-preview env permissions drifted" >&2
  exit 1
}
if grep -Fq 'exclusive-postgres-fixture' "$tmp/log" \
  || grep -Fq 'exclusive-redis-fixture' "$tmp/log"; then
  echo "runtime-preview generator printed an infrastructure secret" >&2
  exit 1
fi

python3 - "$tmp/generated/runtime-preview.env" <<'PY'
import base64
import json
import pathlib
import re
import sys

values = dict(
    line.split("=", 1)
    for line in pathlib.Path(sys.argv[1]).read_text().splitlines()
    if line
)
assert values["APPSELL_POSTGRES_ADMIN_USER"] == "postgres"
assert values["APPSELL_POSTGRES_ADMIN_PASSWORD"] == "exclusive-postgres-fixture"
assert values["APPSELL_POSTGRES_DEFAULT_DATABASE"] == "appsell"
assert values["APPSELL_REDIS_PASSWORD"] == "exclusive-redis-fixture"
assert re.fullmatch(r"[0-9a-f]{48}", values["APPSELL_MIGRATOR_PASSWORD"])
assert re.fullmatch(r"[0-9a-f]{48}", values["APPSELL_RUNTIME_PASSWORD"])
assert re.fullmatch(r"[0-9a-f]{64}", values["APPSELL_CONTEXT_SIGNING_KEY_HEX"])
assert re.fullmatch(r"[0-9a-f]{64}", values["APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX"])
assert values["APPSELL_MIGRATOR_PASSWORD"] != values["APPSELL_RUNTIME_PASSWORD"]
assert values["APPSELL_CONTEXT_SIGNING_KEY_HEX"] != values["APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX"]
for name in (
    "APPSELL_API_CONTEXT_HMAC_KEY_B64",
    "APPSELL_SERVICE_CREDENTIAL_B64",
    "APPSELL_SESSION_HMAC_KEY_B64",
    "APPSELL_CSRF_HMAC_KEY_B64",
):
    assert len(base64.b64decode(values[name], validate=True)) == 32
for name in (
    "APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON",
    "APPSELL_VAULT_LOCAL_KEYRING_B64_JSON",
):
    parsed = json.loads(values[name])
    assert len(parsed) == 1
    assert parsed[0]["id"] == "runtime-v1"
    assert parsed[0]["active"] is True
    assert len(base64.b64decode(parsed[0]["key"], validate=True)) == 32
PY

if PATH="$tmp/bin:$PATH" "$here/generate-runtime-preview-env.sh" \
  "$tmp/generated" 0123456789abcdef0123456789abcdef01234567 >/dev/null 2>&1; then
  echo "runtime-preview generator overwrote an existing secret directory" >&2
  exit 1
fi

echo "EasyPanel runtime-preview env generation: passed"
