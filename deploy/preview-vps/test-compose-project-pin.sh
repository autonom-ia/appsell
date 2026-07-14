#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for script in preflight.sh up.sh verify.sh teardown.sh; do
  file="$here/$script"
  grep -Fq -- '--project-name "$PREVIEW_PROJECT_NAME"' "$file" || {
    echo "$script does not pin the exact Compose project name" >&2
    exit 1
  }
  grep -Fq -- 'unset COMPOSE_PROJECT_NAME COMPOSE_FILE COMPOSE_PROFILES COMPOSE_ENV_FILES' "$file" || {
    echo "$script does not sanitize Compose control environment" >&2
    exit 1
  }
done
for script in load-images.sh up.sh; do
  grep -Fq -- 'AUTONOMIA_PREVIEW_ORCHESTRATED' "$here/$script" || {
    echo "$script does not require the fail-closed activation orchestrator" >&2
    exit 1
  }
done
grep -Fq -- 'up -d --no-build --pull never --wait' "$here/up.sh" || {
  echo "up.sh does not forbid implicit image pulls" >&2
  exit 1
}
grep -Fq -- 'run --pull never' "$here/verify.sh" || {
  echo "verify.sh does not forbid implicit E2E image pulls" >&2
  exit 1
}
grep -Fq -- 'ExecStart=/usr/bin/timeout --signal=TERM --kill-after=30s 10m' "$here/schedule-expiry.sh" || {
  echo "Expiry teardown does not have a hard execution timeout" >&2
  exit 1
}
grep -Fq -- "'TimeoutStartSec=11min'" "$here/schedule-expiry.sh" || {
  echo "Expiry systemd unit does not have an explicit start timeout" >&2
  exit 1
}
service_count="$(awk '/^services:/{inside=1; next} /^networks:/{inside=0} inside && /^  [a-z][a-z0-9-]*:$/ {count++} END {print count+0}' "$here/compose.preview.yaml")"
pull_policy_count="$(grep -c '^    pull_policy: never$' "$here/compose.preview.yaml")"
[[ "$service_count" == "11" && "$pull_policy_count" == "$service_count" ]] || {
  echo "Every Compose service must use pull_policy: never" >&2
  exit 1
}
for script in activate-preview.sh load-images.sh preflight.sh up.sh verify.sh teardown.sh remove-loaded-images.sh generate-preview-env.sh snapshot-existing.sh; do
  grep -Fq -- 'pin_local_preview_docker' "$here/$script" || {
    echo "$script does not pin the local Docker daemon" >&2
    exit 1
  }
done
pending_line="$(grep -nF 'printf '\''%s||%s\n'\'' "$image" "$sha" >>"$record_file"' "$here/load-images.sh" | cut -d: -f1)"
load_line="$(grep -nF 'docker image load --input' "$here/load-images.sh" | cut -d: -f1)"
[[ "$pending_line" =~ ^[0-9]+$ && "$load_line" =~ ^[0-9]+$ && "$pending_line" -lt "$load_line" ]] || {
  echo "load-images does not persist ownership intent before docker image load" >&2
  exit 1
}
grep -Fq -- 'trap cleanup_on_error ERR INT TERM EXIT' "$here/load-images.sh" || {
  echo "load-images does not cover errors, signals and early exit" >&2
  exit 1
}
grep -Fq -- 'resolved_project=' "$here/preflight.sh" || {
  echo "preflight does not validate the resolved Compose project name" >&2
  exit 1
}
echo "Compose project namespace pin: passed"
