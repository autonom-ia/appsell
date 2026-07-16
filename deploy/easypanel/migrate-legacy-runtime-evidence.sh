#!/usr/bin/env bash
set -euo pipefail
case $- in *x*) set +x ;; esac

[[ $# -eq 4 ]] || {
  echo "Usage: $0 RUN_ROOT ENV_FILE APPSELL_SOURCE_SHA LEGACY_EVIDENCE_SOURCE_SHA" >&2
  exit 2
}
[[ "${CONFIRM_RUNTIME_PREVIEW_EVIDENCE_MIGRATION:-}" == "sell-autonomia-evidence-v1" ]] || {
  echo "set CONFIRM_RUNTIME_PREVIEW_EVIDENCE_MIGRATION=sell-autonomia-evidence-v1" >&2
  exit 2
}
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-preview-common.sh
source "$here/runtime-preview-common.sh"
runtime_preview_require_root
runtime_preview_require_commands basename docker jq realpath sha256sum stat tar
runtime_preview_assert_run_root "$1" "$3"
runtime_preview_assert_env_file "$2"
legacy_sha="$4"
[[ "$legacy_sha" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid legacy evidence SHA" >&2; exit 2; }
source_sha="$3"
runtime_preview_verify_service_snapshot "$RUNTIME_PREVIEW_RUN_ROOT/protected-services.before.tsv"

volume="$RUNTIME_PREVIEW_EVIDENCE_VOLUME"
archive="$RUNTIME_PREVIEW_RUN_ROOT/legacy-runtime-evidence-${legacy_sha:0:12}.tar.gz"
attestation="$RUNTIME_PREVIEW_RUN_ROOT/legacy-runtime-evidence-${legacy_sha:0:12}.json"
temporary=""
attestation_temporary=""
cleanup() {
  [[ -z "$temporary" ]] || rm -f -- "$temporary"
  [[ -z "$attestation_temporary" ]] || rm -f -- "$attestation_temporary"
}
trap cleanup EXIT

if inspect="$(runtime_preview_volume_inspect "$volume")"; then
  if runtime_preview_verify_owned_volume \
    "$volume" runtime_evidence runtime-e2e-evidence yes >/dev/null 2>&1; then
    volume_state=stable
    mountpoint=""
  else
    jq -e \
      --arg volume "$volume" --arg compose_project "$RUNTIME_PREVIEW_COMPOSE_PROJECT" \
      --arg legacy_sha "$legacy_sha" '
        type == "array" and length == 1 and
        .[0].Name == $volume and .[0].Driver == "local" and .[0].Scope == "local" and
        .[0].Labels["com.docker.compose.project"] == $compose_project and
        .[0].Labels["com.docker.compose.volume"] == "runtime_evidence" and
        .[0].Labels["com.autonomia.project"] == "sell-autonomia" and
        .[0].Labels["com.autonomia.issue"] == "77" and
        .[0].Labels["com.autonomia.environment"] == "local-ci-easypanel-preview" and
        .[0].Labels["com.autonomia.source-sha"] == $legacy_sha and
        .[0].Labels["com.autonomia.purpose"] == null and
        ((.[0].Labels | keys) - [
          "com.docker.compose.config-hash", "com.docker.compose.project",
          "com.docker.compose.version", "com.docker.compose.volume",
          "com.autonomia.project", "com.autonomia.issue",
          "com.autonomia.environment", "com.autonomia.source-sha"
        ] | length == 0)
      ' <<<"$inspect" >/dev/null || {
      echo "evidence volume is neither the approved legacy nor stable volume" >&2
      exit 1
    }
    consumers="$(docker ps -aq --filter "volume=$volume")"
    [[ -z "$consumers" ]] || {
      echo "legacy evidence volume still has container consumers" >&2
      exit 1
    }
    mountpoint="$(jq -er '.[0].Mountpoint' <<<"$inspect")"
    [[ "$mountpoint" == "/var/lib/docker/volumes/$volume/_data" && \
      "$(realpath -e "$mountpoint")" == "$mountpoint" ]] || {
      echo "legacy evidence volume mountpoint drifted" >&2
      exit 1
    }
    volume_state=legacy
  fi
else
  volume_status=$?
  [[ "$volume_status" == "3" ]] || exit "$volume_status"
  volume_state=absent
  mountpoint=""
fi

if [[ -e "$archive" || -L "$archive" ]]; then
  runtime_preview_assert_protected_file "$archive"
  tar -tzf "$archive" >/dev/null
else
  [[ "$volume_state" == "legacy" && ! -e "$attestation" && ! -L "$attestation" ]] || {
    echo "a new legacy archive requires the still-owned legacy volume" >&2
    exit 1
  }
  temporary="$(mktemp "$RUNTIME_PREVIEW_RUN_ROOT/.legacy-evidence.XXXXXX")"
  chmod 0600 "$temporary"
  tar --one-file-system --numeric-owner -czf "$temporary" -C "$mountpoint" .
  chown root:root "$temporary"
  mv -f -- "$temporary" "$archive"
  temporary=""
  chmod 0600 "$archive"
  tar -tzf "$archive" >/dev/null
fi
archive_sha256="$(sha256sum "$archive" | awk '{print $1}')"
if [[ -e "$attestation" || -L "$attestation" ]]; then
  runtime_preview_assert_protected_file "$attestation"
  jq -e \
    --arg volume "$volume" --arg legacy_source_sha "$legacy_sha" \
    --arg replacement_source_sha "$source_sha" --arg archive "$(basename "$archive")" \
    --arg archive_sha256 "$archive_sha256" '
      .schema_version == "autonomia.easypanel-legacy-evidence.v1" and
      .volume == $volume and .legacy_source_sha == $legacy_source_sha and
      .replacement_source_sha == $replacement_source_sha and .archive == $archive and
      .archive_sha256 == $archive_sha256 and
      (.archived_at | type == "string" and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T"))
    ' "$attestation" >/dev/null || {
    echo "legacy evidence attestation mismatch" >&2
    exit 1
  }
else
  attestation_temporary="$(mktemp "$RUNTIME_PREVIEW_RUN_ROOT/.legacy-evidence-attestation.XXXXXX")"
  chmod 0600 "$attestation_temporary"
  jq -n \
    --arg schema_version autonomia.easypanel-legacy-evidence.v1 \
    --arg volume "$volume" --arg legacy_source_sha "$legacy_sha" \
    --arg replacement_source_sha "$source_sha" --arg archive "$(basename "$archive")" \
    --arg archive_sha256 "$archive_sha256" --arg archived_at "$(date -u +%FT%TZ)" \
    '{schema_version:$schema_version,volume:$volume,legacy_source_sha:$legacy_source_sha,
      replacement_source_sha:$replacement_source_sha,archive:$archive,
      archive_sha256:$archive_sha256,archived_at:$archived_at}' >"$attestation_temporary"
  chown root:root "$attestation_temporary"
  mv -f -- "$attestation_temporary" "$attestation"
  attestation_temporary=""
  chmod 0600 "$attestation"
fi

if [[ "$volume_state" == "legacy" ]]; then
  if ! docker volume rm "$volume" >/dev/null; then
    if runtime_preview_volume_inspect "$volume" >/dev/null 2>&1; then
      echo "legacy evidence volume removal failed" >&2
      exit 1
    else
      status=$?
      [[ "$status" == "3" ]] || exit "$status"
    fi
  fi
fi
if [[ "$volume_state" != "stable" ]]; then
  runtime_preview_ensure_owned_volume "$volume" runtime_evidence runtime-e2e-evidence
fi
runtime_preview_verify_owned_volume "$volume" runtime_evidence runtime-e2e-evidence yes
runtime_preview_verify_service_snapshot "$RUNTIME_PREVIEW_RUN_ROOT/protected-services.before.tsv"
trap - EXIT
cleanup
echo "legacy runtime evidence archived and replaced by the stable owned volume"
