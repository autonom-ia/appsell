#!/usr/bin/env bash
set -euo pipefail

mode=integrated
case $# in
  3)
    mode=runtime-preview
    ;;
  9) ;;
  *)
    echo "Usage: $0 ROOT APPSELL_SHA APPSELL_RUN_ID" >&2
    echo "   or: $0 ROOT APPSELL_SHA AUTH_SHA FOUNDATION_SHA APPSELL_RUN_ID AUTH_RUN_ID FOUNDATION_RUN_ID AUTH_PR AUTH_WORKFLOW_SHA" >&2
    exit 2
    ;;
esac

[[ -d "$1" && ! -L "$1" ]] || { echo "ROOT must be an existing non-symlink directory" >&2; exit 2; }
root="$(cd "$1" && pwd -P)"
appsell_sha="$2"
if [[ "$mode" == "runtime-preview" ]]; then
  appsell_run_id="$3"
  auth_sha=""
  foundation_sha=""
  auth_run_id=""
  foundation_run_id=""
  auth_pr=""
  auth_workflow_sha=""
else
  auth_sha="$3"
  foundation_sha="$4"
  appsell_run_id="$5"
  auth_run_id="$6"
  foundation_run_id="$7"
  auth_pr="$8"
  auth_workflow_sha="$9"
fi
appsell_branch="${APPSELL_ARTIFACT_BRANCH:-infra/66-vps-preview}"
appsell_event="${APPSELL_ARTIFACT_EVENT:-push}"

case "$appsell_branch" in
  infra/66-vps-preview|infra/66-easypanel-rc|infra/77-easypanel-runtime-preview|infra/77-easypanel-runtime-preview-rc) ;;
  *) echo "Unsupported AppSell artifact branch" >&2; exit 2 ;;
esac
case "$appsell_event" in
  push|workflow_dispatch) ;;
  *) echo "Unsupported AppSell artifact event" >&2; exit 2 ;;
esac

for command_name in gh jq unzip python3; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Missing required command: $command_name" >&2
    exit 2
  }
done
shas=("$appsell_sha")
numbers=("$appsell_run_id")
if [[ "$mode" == "integrated" ]]; then
  shas+=("$auth_sha" "$foundation_sha" "$auth_workflow_sha")
  numbers+=("$auth_run_id" "$foundation_run_id" "$auth_pr")
fi
for sha in "${shas[@]}"; do
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "Invalid source SHA" >&2; exit 2; }
done
for number in "${numbers[@]}"; do
  [[ "$number" =~ ^[1-9][0-9]*$ ]] || { echo "Invalid numeric identifier" >&2; exit 2; }
done

download_root="$root/downloads"
upload_root="$root/upload"
[[ ! -e "$download_root" && ! -L "$download_root" ]] || { echo "Refusing to reuse downloads directory" >&2; exit 2; }
[[ ! -e "$upload_root" && ! -L "$upload_root" ]] || { echo "Refusing to overwrite upload directory" >&2; exit 2; }
install -d -m 0700 "$download_root" "$upload_root" "$upload_root/artifacts"

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

file_size() {
  if stat -c '%s' "$1" >/dev/null 2>&1; then
    stat -c '%s' "$1"
  else
    stat -f '%z' "$1"
  fi
}

safe_label() {
  tr '/.' '__' <<<"$1"
}

verify_run() {
  local repo="$1" run_id="$2" workflow_name="$3" workflow_path="$4"
  local event="$5" head_branch="$6" source_sha="$7" require_head_sha="$8"
  local label run_file jobs_file
  label="$(safe_label "$repo")-$run_id"
  run_file="$download_root/$label.run.json"
  jobs_file="$download_root/$label.jobs.json"

  gh api "repos/$repo/actions/runs/$run_id" >"$run_file"
  jq -e \
    --arg run_id "$run_id" \
    --arg repo "$repo" \
    --arg workflow_name "$workflow_name" \
    --arg workflow_path "$workflow_path" \
    --arg event "$event" \
    --arg head_branch "$head_branch" \
    --arg source_sha "$source_sha" \
    --arg require_head_sha "$require_head_sha" '
      .id == ($run_id | tonumber) and
      .name == $workflow_name and
      .path == $workflow_path and
      .event == $event and
      .status == "completed" and
      .conclusion == "success" and
      .head_branch == $head_branch and
      .head_repository.full_name == $repo and
      (.run_attempt | type == "number" and . >= 1) and
      (.actor.login | type == "string" and length > 0) and
      (.triggering_actor.login | type == "string" and length > 0) and
      ($require_head_sha == "no" or .head_sha == $source_sha)
    ' "$run_file" >/dev/null || {
      echo "GitHub run metadata rejected: $repo run $run_id" >&2
      exit 2
    }

  gh api "repos/$repo/actions/runs/$run_id/jobs?per_page=100" >"$jobs_file"
  jq -e '
    (.total_count >= 1) and
    (.total_count == (.jobs | length)) and
    (.jobs | all(.[];
      .status == "completed" and
      .conclusion == "success" and
      .runner_group_name == "GitHub Actions" and
      (.runner_name | type == "string" and startswith("GitHub Actions"))
    ))
  ' "$jobs_file" >/dev/null || {
    echo "Run contains a failed, incomplete or non-GitHub-hosted job: $repo run $run_id" >&2
    exit 2
  }
}

verify_current_auth_approval() {
  local repo=autonom-ia/autonomia-identity-auth
  local run_file pr_file timeline_file reviews_file author actor triggering_actor latest
  run_file="$download_root/$(safe_label "$repo")-$auth_run_id.run.json"
  pr_file="$download_root/auth-pr-$auth_pr.json"
  timeline_file="$download_root/auth-pr-$auth_pr.timeline.json"
  reviews_file="$download_root/auth-pr-$auth_pr.reviews.json"
  gh api "repos/$repo/pulls/$auth_pr" >"$pr_file"
  gh api --paginate --slurp "repos/$repo/issues/$auth_pr/timeline?per_page=100" >"$timeline_file"
  gh api --paginate --slurp "repos/$repo/pulls/$auth_pr/reviews?per_page=100" >"$reviews_file"
  author="$(jq -r '.user.login' "$pr_file")"
  actor="$(jq -r '.actor.login' "$run_file")"
  triggering_actor="$(jq -r '.triggering_actor.login' "$run_file")"
  latest="$(jq '
    add
    | map(select(
      .state == "APPROVED" or
      .state == "CHANGES_REQUESTED" or
      .state == "DISMISSED"
    ))
    | sort_by(.submitted_at)
    | group_by(.user.login)
    | map(last)
  ' "$reviews_file")"
  jq -e \
    --arg repo "$repo" \
    --arg source_sha "$auth_sha" '
      .state == "open" and
      .draft == false and
      .head.sha == $source_sha and
      .head.repo.full_name == $repo and
      .base.ref == "main" and
      .base.repo.full_name == $repo
    ' "$pr_file" >/dev/null || {
      echo "Auth source PR is not an open, ready, main-targeted PR at the exact source SHA" >&2
      exit 2
    }
  jq -e '
    add
    | ([.[] | select(.event == "base_ref_changed")] | length) == 0
  ' "$timeline_file" >/dev/null || {
    echo "Auth source PR was retargeted after creation" >&2
    exit 2
  }
  jq -e \
    --arg author "$author" \
    --arg actor "$actor" \
    --arg triggering_actor "$triggering_actor" \
    --arg source_sha "$auth_sha" '
      ([.[] | select(.state == "CHANGES_REQUESTED")] | length) == 0 and
      ([.[]
        | select(
          .state == "APPROVED" and
          .commit_id == $source_sha and
          .user.login != $author and
          .user.login != $actor and
          .user.login != $triggering_actor
        )
      ] | length) >= 1
    ' <<<"$latest" >/dev/null || {
      echo "Auth source PR no longer has an independent current-SHA approval" >&2
      exit 2
    }
}

download_and_verify_artifact() {
  local repo="$1" run_id="$2" workflow_path="$3" head_branch="$4" source_sha="$5"
  local event="$6" artifact_name="$7" subject_filename="$8" approval_pr="$9"
  local label artifacts_file artifact_file artifact_id archive api_digest archive_digest
  local api_size archive_size subject_size subject_limit
  local expected_entries actual_entries provenance_file subject_file subject_digest run_attempt expected_ref

  label="$(safe_label "$repo")-$run_id-$(safe_label "$artifact_name")"
  artifacts_file="$download_root/$label.artifacts.json"
  artifact_file="$download_root/$label.artifact.json"
  archive="$download_root/$label.zip"
  provenance_file="$download_root/$label.provenance.json"
  subject_file="$download_root/$subject_filename"

  gh api "repos/$repo/actions/runs/$run_id/artifacts?per_page=100" >"$artifacts_file"
  jq -e --arg artifact_name "$artifact_name" '
    .total_count == (.artifacts | length) and
    ([.artifacts[] | select(.name == $artifact_name and .expired == false)] | length == 1)
  ' "$artifacts_file" >/dev/null || {
    echo "Expected exactly one live artifact named $artifact_name" >&2
    exit 2
  }
  jq --arg artifact_name "$artifact_name" '
    [.artifacts[] | select(.name == $artifact_name and .expired == false)][0]
  ' "$artifacts_file" >"$artifact_file"
  jq -e --arg run_id "$run_id" '
    (.id | type == "number") and
    (.digest | test("^sha256:[0-9a-f]{64}$")) and
    (.size_in_bytes | type == "number" and . > 0 and . <= (10 * 1024 * 1024 * 1024)) and
    .workflow_run.id == ($run_id | tonumber)
  ' "$artifact_file" >/dev/null || {
    echo "Artifact API metadata rejected: $artifact_name" >&2
    exit 2
  }

  artifact_id="$(jq -r '.id' "$artifact_file")"
  api_digest="$(jq -r '.digest | sub("^sha256:"; "")' "$artifact_file")"
  api_size="$(jq -r '.size_in_bytes' "$artifact_file")"
  gh api "repos/$repo/actions/artifacts/$artifact_id/zip" >"$archive"
  archive_size="$(file_size "$archive")"
  [[ "$archive_size" == "$api_size" ]] || {
    echo "GitHub artifact archive size mismatch: $artifact_name" >&2
    exit 2
  }
  archive_digest="$(sha256_file "$archive")"
  [[ "$archive_digest" == "$api_digest" ]] || {
    echo "GitHub artifact archive digest mismatch: $artifact_name" >&2
    exit 2
  }

  expected_entries="$(printf '%s\n' provenance.json "$subject_filename" | LC_ALL=C sort)"
  actual_entries="$(unzip -Z1 "$archive" | LC_ALL=C sort)"
  [[ "$actual_entries" == "$expected_entries" ]] || {
    echo "Artifact ZIP has an unexpected tree: $artifact_name" >&2
    exit 2
  }
  if [[ "$subject_filename" == config-*.tar.gz ]]; then
    subject_limit=$((64 * 1024 * 1024))
  else
    subject_limit=$((8 * 1024 * 1024 * 1024))
  fi
  python3 - "$archive" "$subject_filename" "$subject_limit" <<'PY'
import sys
import zipfile

archive, subject, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
with zipfile.ZipFile(archive) as bundle:
    info = bundle.getinfo(subject)
    if info.file_size <= 0 or info.file_size > limit:
        raise SystemExit("artifact subject declared size rejected before extraction")
PY
  unzip -p "$archive" provenance.json >"$provenance_file"
  unzip -p "$archive" "$subject_filename" >"$subject_file"
  chmod 0600 "$provenance_file" "$subject_file"
  subject_size="$(file_size "$subject_file")"
  (( subject_size > 0 && subject_size <= subject_limit )) || {
    echo "Artifact subject size rejected: $artifact_name" >&2
    exit 2
  }

  subject_digest="$(sha256_file "$subject_file")"
  run_attempt="$(jq -r '.run_attempt | tostring' "$download_root/$(safe_label "$repo")-$run_id.run.json")"
  expected_ref="$repo/$workflow_path@refs/heads/$head_branch"
  jq -e \
    --arg repository "$repo" \
    --arg workflow_path "$workflow_path" \
    --arg workflow_ref "$expected_ref" \
    --arg source_sha "$source_sha" \
    --arg run_id "$run_id" \
    --arg run_attempt "$run_attempt" \
    --arg event "$event" \
    --arg artifact_name "$artifact_name" \
    --arg subject_filename "$subject_filename" \
    --arg subject_sha256 "$subject_digest" \
    --arg approval_pr "$approval_pr" '
      .schema_version == "autonomia.preview-artifact.v1" and
      .repository == $repository and
      .workflow_path == $workflow_path and
      .workflow_ref == $workflow_ref and
      .source_sha == $source_sha and
      .run_id == $run_id and
      .run_attempt == $run_attempt and
      .event == $event and
      .runner_environment == "github-hosted" and
      .artifact_name == $artifact_name and
      .subject_filename == $subject_filename and
      .subject_sha256 == $subject_sha256 and
      ($approval_pr == "" or .approval_pr == $approval_pr)
    ' "$provenance_file" >/dev/null || {
      echo "Embedded provenance rejected: $artifact_name" >&2
      exit 2
    }
}

verify_run \
  autonom-ia/appsell "$appsell_run_id" "VPS preview artifacts" \
  .github/workflows/preview-images.yml "$appsell_event" "$appsell_branch" "$appsell_sha" yes
if [[ "$mode" == "integrated" ]]; then
  verify_run \
    autonom-ia/autonomia-identity-foundation "$foundation_run_id" \
    "Foundation Local CI preview artifact" .github/workflows/preview-image.yml \
    push infra/37-preview-artifact "$foundation_sha" yes
  verify_run \
    autonom-ia/autonomia-identity-auth "$auth_run_id" \
    "Trusted Identity Auth preview artifact" \
    .github/workflows/trusted-preview-artifact.yml workflow_dispatch main "$auth_workflow_sha" yes
  verify_current_auth_approval
fi

download_and_verify_artifact \
  autonom-ia/appsell "$appsell_run_id" .github/workflows/preview-images.yml \
  "$appsell_branch" "$appsell_sha" "$appsell_event" \
  "appsell-vps-preview-config-$appsell_sha" "config-$appsell_sha.tar.gz" ""
download_and_verify_artifact \
  autonom-ia/appsell "$appsell_run_id" .github/workflows/preview-images.yml \
  "$appsell_branch" "$appsell_sha" "$appsell_event" \
  "appsell-vps-preview-sell-$appsell_sha" "sell-$appsell_sha.tar" ""
download_and_verify_artifact \
  autonom-ia/appsell "$appsell_run_id" .github/workflows/preview-images.yml \
  "$appsell_branch" "$appsell_sha" "$appsell_event" \
  "appsell-vps-preview-domain-$appsell_sha" "domain-$appsell_sha.tar" ""
download_and_verify_artifact \
  autonom-ia/appsell "$appsell_run_id" .github/workflows/preview-images.yml \
  "$appsell_branch" "$appsell_sha" "$appsell_event" \
  "appsell-vps-preview-base-alpine-$appsell_sha" "base-alpine-$appsell_sha.tar" ""
if [[ "$mode" == "integrated" ]]; then
  download_and_verify_artifact \
    autonom-ia/appsell "$appsell_run_id" .github/workflows/preview-images.yml \
    "$appsell_branch" "$appsell_sha" "$appsell_event" \
    "appsell-vps-preview-base-postgres-$appsell_sha" "base-postgres-$appsell_sha.tar" ""
  download_and_verify_artifact \
    autonom-ia/appsell "$appsell_run_id" .github/workflows/preview-images.yml \
    "$appsell_branch" "$appsell_sha" "$appsell_event" \
    "appsell-vps-preview-base-redis-$appsell_sha" "base-redis-$appsell_sha.tar" ""
  download_and_verify_artifact \
    autonom-ia/autonomia-identity-foundation "$foundation_run_id" \
    .github/workflows/preview-image.yml infra/37-preview-artifact "$foundation_sha" push \
    "identity-foundation-preview-$foundation_sha" "foundation-$foundation_sha.tar" ""
  download_and_verify_artifact \
    autonom-ia/autonomia-identity-auth "$auth_run_id" \
    .github/workflows/trusted-preview-artifact.yml main "$auth_sha" workflow_dispatch \
    "identity-auth-preview-$auth_sha" "auth-$auth_sha.tar" "$auth_pr"
fi

cp \
  "$download_root/sell-$appsell_sha.tar" \
  "$download_root/domain-$appsell_sha.tar" \
  "$download_root/base-alpine-$appsell_sha.tar" \
  "$upload_root/artifacts/"
if [[ "$mode" == "integrated" ]]; then
  cp \
    "$download_root/base-postgres-$appsell_sha.tar" \
    "$download_root/base-redis-$appsell_sha.tar" \
    "$download_root/auth-$auth_sha.tar" \
    "$download_root/foundation-$foundation_sha.tar" \
    "$upload_root/artifacts/"
fi
(
  cd "$upload_root/artifacts"
  artifacts=("sell-$appsell_sha.tar" "domain-$appsell_sha.tar" "base-alpine-$appsell_sha.tar")
  if [[ "$mode" == "integrated" ]]; then
    artifacts+=("base-postgres-$appsell_sha.tar" "base-redis-$appsell_sha.tar")
    artifacts+=("auth-$auth_sha.tar" "foundation-$foundation_sha.tar")
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${artifacts[@]}" > SHA256SUMS
  else
    shasum -a 256 "${artifacts[@]}" > SHA256SUMS
  fi
)
python3 - "$download_root/config-$appsell_sha.tar.gz" "$mode" <<'PY'
import pathlib
import sys
import tarfile

archive = pathlib.Path(sys.argv[1])
mode = sys.argv[2]
with tarfile.open(archive, mode="r:gz") as bundle:
    members = bundle.getmembers()
    if not members:
        raise SystemExit("config archive is empty")
    if len(members) > 256:
        raise SystemExit("config archive contains too many members")
    if sum(member.size for member in members) > 64 * 1024 * 1024:
        raise SystemExit("config archive expands beyond 64 MiB")
    by_name = {member.name.rstrip("/"): member for member in members}
    required_files = {
        "config/SHA256SUMS": False,
        "config/easypanel/README.md": False,
        "config/easypanel/harden-sell-services.sh": True,
        "config/easypanel/verify-sell-isolation.sh": True,
    }
    if mode == "runtime-preview":
        required_files.update({
        "config/easypanel/bootstrap-runtime-preview.sh": True,
        "config/easypanel/compose.runtime-preview.yaml": False,
        "config/easypanel/generate-runtime-preview-env.sh": True,
        "config/easypanel/load-runtime-preview-images.sh": True,
        "config/easypanel/preflight-runtime-preview.sh": True,
        "config/easypanel/remove-runtime-preview-images.sh": True,
        "config/easypanel/run-runtime-preview-e2e.sh": True,
        "config/easypanel/runtime-preview-common.sh": False,
        "config/easypanel/retire-runtime-preview-credentials.sh": True,
        "config/easypanel/runtime-preview.env.example": False,
        "config/easypanel/teardown-runtime-preview.sh": True,
        "config/easypanel/test-runtime-preview-common.sh": True,
        "config/easypanel/test-runtime-preview-env.sh": True,
        "config/easypanel/test-runtime-preview-lifecycle.sh": True,
        "config/easypanel/test-runtime-preview.sh": True,
        "config/easypanel/up-runtime-preview.sh": True,
        "config/easypanel/verify-runtime-preview.sh": True,
        })
    for member in members:
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit("config archive contains an unsafe path")
        if not path.parts or path.parts[0] != "config":
            raise SystemExit(f"config archive contains an unexpected root: {member.name}")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise SystemExit("config archive contains a special or linked entry")
    for name, executable in required_files.items():
        member = by_name.get(name)
        if member is None or not member.isfile():
            raise SystemExit(f"config archive is missing required regular file: {name}")
        if executable and member.mode & 0o111 == 0:
            raise SystemExit(f"config archive script is not executable: {name}")
PY
tar -xzf "$download_root/config-$appsell_sha.tar.gz" -C "$upload_root"
(
  cd "$upload_root/config"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum --check SHA256SUMS
  else
    shasum -a 256 --check SHA256SUMS
  fi
) >/dev/null
if [[ "$mode" == "runtime-preview" ]]; then
  echo "Four exact GitHub-hosted runtime-preview artifacts verified; upload/ is ready."
else
  echo "Eight exact GitHub-hosted artifacts verified by run metadata, API digest and embedded checksums; upload/ is ready."
fi
