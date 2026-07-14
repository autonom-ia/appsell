#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
verifier="$here/verify-downloads-local.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

for command_name in jq zip tar python3; do
  command -v "$command_name" >/dev/null 2>&1 || exit 2
done

appsell_sha=1111111111111111111111111111111111111111
foundation_sha=2222222222222222222222222222222222222222
auth_sha=3333333333333333333333333333333333333333
auth_workflow_sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
appsell_run=101
foundation_run=102
auth_run=103
auth_pr=28

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

make_run() {
  local repo="$1" run_id="$2" name="$3" path="$4" event="$5" branch="$6" head_sha="$7"
  local directory="$tmp/api/$repo/$run_id"
  install -d "$directory"
  jq -n \
    --argjson id "$run_id" --arg name "$name" --arg path "$path" \
    --arg event "$event" --arg branch "$branch" --arg head_sha "$head_sha" \
    --arg repo "$repo" \
    '{
      id: $id, name: $name, path: $path, event: $event,
      status: "completed", conclusion: "success",
      head_branch: $branch, head_sha: $head_sha, run_attempt: 1,
      actor: {login: "operator"}, triggering_actor: {login: "dispatcher"},
      head_repository: {full_name: $repo}
    }' >"$directory/run.json"
  jq -n '{
    total_count: 1,
    jobs: [{
      status: "completed", conclusion: "success",
      runner_group_name: "GitHub Actions",
      runner_name: "GitHub Actions 1000000001"
    }]
  }' >"$directory/jobs.json"
}

make_artifact() {
  local repo="$1" path="$2" branch="$3" source_sha="$4" run_id="$5"
  local event="$6" artifact_name="$7" subject="$8" artifact_id="$9"
  local approval_pr="${10}"
  local directory="$tmp/api/$repo/$run_id"
  local artifact_root="$tmp/artifact-$artifact_id"
  install -d "$artifact_root"
  cp "$subject" "$artifact_root/"
  local filename subject_sha archive digest archive_size
  filename="$(basename "$subject")"
  subject_sha="$(sha256_file "$subject")"
  jq -n \
    --arg schema_version autonomia.preview-artifact.v1 \
    --arg repository "$repo" --arg workflow_path "$path" \
    --arg workflow_ref "$repo/$path@refs/heads/$branch" \
    --arg source_sha "$source_sha" --arg run_id "$run_id" \
    --arg run_attempt 1 --arg event "$event" \
    --arg runner_environment github-hosted --arg artifact_name "$artifact_name" \
    --arg subject_filename "$filename" --arg subject_sha256 "$subject_sha" \
    --arg approval_pr "$approval_pr" \
    '{
      schema_version: $schema_version, repository: $repository,
      workflow_path: $workflow_path, workflow_ref: $workflow_ref,
      source_sha: $source_sha, run_id: $run_id, run_attempt: $run_attempt,
      event: $event, runner_environment: $runner_environment,
      artifact_name: $artifact_name, subject_filename: $subject_filename,
      subject_sha256: $subject_sha256
    } + (if $approval_pr == "" then {} else {approval_pr: $approval_pr} end)' \
    >"$artifact_root/provenance.json"
  archive="$tmp/api/artifact-$artifact_id.zip"
  (
    cd "$artifact_root"
    zip -q "$archive" "$filename" provenance.json
  )
  digest="$(sha256_file "$archive")"
  archive_size="$(wc -c <"$archive" | tr -d ' ')"
  jq -n \
    --argjson id "$artifact_id" --arg name "$artifact_name" \
    --arg digest "sha256:$digest" --argjson run_id "$run_id" \
    --argjson size_in_bytes "$archive_size" \
    '{
      id: $id, name: $name, digest: $digest, size_in_bytes: $size_in_bytes,
      expired: false,
      workflow_run: {id: $run_id}
    }' >"$directory/artifact-$artifact_id.json"
}

install -d "$tmp/subjects/config"
printf 'preview fixture\n' >"$tmp/subjects/config/README"
(
  cd "$tmp/subjects/config"
  sha256_file README >"$tmp/readme.digest"
  printf '%s  README\n' "$(cat "$tmp/readme.digest")" >SHA256SUMS
)
COPYFILE_DISABLE=1 tar -czf "$tmp/subjects/config-$appsell_sha.tar.gz" -C "$tmp/subjects" config
printf 'sell image\n' >"$tmp/subjects/sell-$appsell_sha.tar"
printf 'domain image\n' >"$tmp/subjects/domain-$appsell_sha.tar"
printf 'alpine image\n' >"$tmp/subjects/base-alpine-$appsell_sha.tar"
printf 'postgres image\n' >"$tmp/subjects/base-postgres-$appsell_sha.tar"
printf 'redis image\n' >"$tmp/subjects/base-redis-$appsell_sha.tar"
printf 'foundation image\n' >"$tmp/subjects/foundation-$foundation_sha.tar"
printf 'auth image\n' >"$tmp/subjects/auth-$auth_sha.tar"

make_run autonom-ia/appsell "$appsell_run" "VPS preview artifacts" \
  .github/workflows/preview-images.yml push infra/66-vps-preview "$appsell_sha"
make_run autonom-ia/autonomia-identity-foundation "$foundation_run" \
  "Foundation Local CI preview artifact" .github/workflows/preview-image.yml \
  push infra/37-preview-artifact "$foundation_sha"
make_run autonom-ia/autonomia-identity-auth "$auth_run" \
  "Trusted Identity Auth preview artifact" \
  .github/workflows/trusted-preview-artifact.yml workflow_dispatch main "$auth_workflow_sha"

jq -n \
  --arg source_sha "$auth_sha" \
  '{
    state: "open", draft: false, user: {login: "author"},
    head: {sha: $source_sha, repo: {full_name: "autonom-ia/autonomia-identity-auth"}},
    base: {ref: "main", repo: {full_name: "autonom-ia/autonomia-identity-auth"}}
  }' >"$tmp/api/auth-pr-$auth_pr.json"
printf '[[]]\n' >"$tmp/api/auth-pr-$auth_pr.timeline.json"
jq -n \
  --arg source_sha "$auth_sha" \
  '[[{
    state: "APPROVED", commit_id: $source_sha,
    submitted_at: "2026-07-14T18:00:00Z", user: {login: "reviewer"}
  }]]' >"$tmp/api/auth-pr-$auth_pr.reviews.json"

make_artifact autonom-ia/appsell .github/workflows/preview-images.yml \
  infra/66-vps-preview "$appsell_sha" "$appsell_run" push \
  "appsell-vps-preview-config-$appsell_sha" "$tmp/subjects/config-$appsell_sha.tar.gz" 1001 ""
make_artifact autonom-ia/appsell .github/workflows/preview-images.yml \
  infra/66-vps-preview "$appsell_sha" "$appsell_run" push \
  "appsell-vps-preview-sell-$appsell_sha" "$tmp/subjects/sell-$appsell_sha.tar" 1002 ""
make_artifact autonom-ia/appsell .github/workflows/preview-images.yml \
  infra/66-vps-preview "$appsell_sha" "$appsell_run" push \
  "appsell-vps-preview-domain-$appsell_sha" "$tmp/subjects/domain-$appsell_sha.tar" 1003 ""
make_artifact autonom-ia/appsell .github/workflows/preview-images.yml \
  infra/66-vps-preview "$appsell_sha" "$appsell_run" push \
  "appsell-vps-preview-base-alpine-$appsell_sha" "$tmp/subjects/base-alpine-$appsell_sha.tar" 1006 ""
make_artifact autonom-ia/appsell .github/workflows/preview-images.yml \
  infra/66-vps-preview "$appsell_sha" "$appsell_run" push \
  "appsell-vps-preview-base-postgres-$appsell_sha" "$tmp/subjects/base-postgres-$appsell_sha.tar" 1007 ""
make_artifact autonom-ia/appsell .github/workflows/preview-images.yml \
  infra/66-vps-preview "$appsell_sha" "$appsell_run" push \
  "appsell-vps-preview-base-redis-$appsell_sha" "$tmp/subjects/base-redis-$appsell_sha.tar" 1008 ""
make_artifact autonom-ia/autonomia-identity-foundation .github/workflows/preview-image.yml \
  infra/37-preview-artifact "$foundation_sha" "$foundation_run" push \
  "identity-foundation-preview-$foundation_sha" "$tmp/subjects/foundation-$foundation_sha.tar" 1004 ""
make_artifact autonom-ia/autonomia-identity-auth .github/workflows/trusted-preview-artifact.yml \
  main "$auth_sha" "$auth_run" workflow_dispatch \
  "identity-auth-preview-$auth_sha" "$tmp/subjects/auth-$auth_sha.tar" 1005 "$auth_pr"

for spec in \
  "autonom-ia/appsell/$appsell_run" \
  "autonom-ia/autonomia-identity-foundation/$foundation_run" \
  "autonom-ia/autonomia-identity-auth/$auth_run"; do
  directory="$tmp/api/$spec"
  jq -s '{total_count: length, artifacts: .}' "$directory"/artifact-*.json >"$directory/artifacts.json"
done

install -d "$tmp/bin"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == api ]]
endpoint="${!#}"
case "$endpoint" in
  repos/autonom-ia/autonomia-identity-auth/pulls/*/reviews\?per_page=100)
    pr="${endpoint#*/pulls/}"
    pr="${pr%/reviews?per_page=100}"
    cat "$MOCK_ROOT/api/auth-pr-$pr.reviews.json"
    ;;
  repos/autonom-ia/autonomia-identity-auth/issues/*/timeline\?per_page=100)
    pr="${endpoint#*/issues/}"
    pr="${pr%/timeline?per_page=100}"
    cat "$MOCK_ROOT/api/auth-pr-$pr.timeline.json"
    ;;
  repos/autonom-ia/autonomia-identity-auth/pulls/*)
    pr="${endpoint##*/}"
    cat "$MOCK_ROOT/api/auth-pr-$pr.json"
    ;;
  repos/*/actions/runs/*/jobs\?per_page=100)
    prefix="${endpoint%/jobs?per_page=100}"
    spec="${prefix#repos/}"
    run_id="${spec##*/}"
    repo="${spec%/actions/runs/*}"
    cat "$MOCK_ROOT/api/$repo/$run_id/jobs.json"
    ;;
  repos/*/actions/runs/*/artifacts\?per_page=100)
    prefix="${endpoint%/artifacts?per_page=100}"
    spec="${prefix#repos/}"
    run_id="${spec##*/}"
    repo="${spec%/actions/runs/*}"
    cat "$MOCK_ROOT/api/$repo/$run_id/artifacts.json"
    ;;
  repos/*/actions/runs/*)
    spec="${endpoint#repos/}"
    run_id="${spec##*/}"
    repo="${spec%/actions/runs/*}"
    cat "$MOCK_ROOT/api/$repo/$run_id/run.json"
    ;;
  repos/*/actions/artifacts/*/zip)
    id="${endpoint#*/actions/artifacts/}"
    id="${id%/zip}"
    cat "$MOCK_ROOT/api/artifact-$id.zip"
    ;;
  *)
    echo "unexpected mock endpoint: $endpoint" >&2
    exit 2
    ;;
esac
GH
chmod 0700 "$tmp/bin/gh"

install -d "$tmp/success"
PATH="$tmp/bin:$PATH" MOCK_ROOT="$tmp" "$verifier" \
  "$tmp/success" "$appsell_sha" "$auth_sha" "$foundation_sha" \
  "$appsell_run" "$auth_run" "$foundation_run" "$auth_pr" "$auth_workflow_sha" >/dev/null
test -f "$tmp/success/upload/config/SHA256SUMS"
test -f "$tmp/success/upload/artifacts/auth-$auth_sha.tar"
test -f "$tmp/success/upload/artifacts/base-postgres-$appsell_sha.tar"
upload_mode="$(stat -c '%a' "$tmp/success/upload" 2>/dev/null || stat -f '%Lp' "$tmp/success/upload")"
[[ "$upload_mode" == "700" ]] || { echo "Verified upload root is not mode 0700" >&2; exit 1; }

install -d "$tmp/workflow-sha-failure"
if PATH="$tmp/bin:$PATH" MOCK_ROOT="$tmp" "$verifier" \
  "$tmp/workflow-sha-failure" "$appsell_sha" "$auth_sha" "$foundation_sha" \
  "$appsell_run" "$auth_run" "$foundation_run" "$auth_pr" \
  bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb >/dev/null 2>&1; then
  echo "Verifier accepted the wrong trusted Auth workflow SHA" >&2
  exit 1
fi

auth_jobs="$tmp/api/autonom-ia/autonomia-identity-auth/$auth_run/jobs.json"
cp "$auth_jobs" "$tmp/auth-jobs-hosted.json"
jq '.jobs[0].runner_group_name = "private" | .jobs[0].runner_name = "self-hosted-1"' \
  "$tmp/auth-jobs-hosted.json" >"$auth_jobs"
install -d "$tmp/runner-failure"
if PATH="$tmp/bin:$PATH" MOCK_ROOT="$tmp" "$verifier" \
  "$tmp/runner-failure" "$appsell_sha" "$auth_sha" "$foundation_sha" \
  "$appsell_run" "$auth_run" "$foundation_run" "$auth_pr" "$auth_workflow_sha" >/dev/null 2>&1; then
  echo "Verifier accepted a self-hosted Auth build job" >&2
  exit 1
fi
cp "$tmp/auth-jobs-hosted.json" "$auth_jobs"

cp "$tmp/api/auth-pr-$auth_pr.reviews.json" "$tmp/auth-reviews-approved.json"
jq '
  .[0] += [
    range(0; 99) as $index
    | {
        state: "COMMENTED",
        commit_id: "3333333333333333333333333333333333333333",
        submitted_at: "2026-07-14T18:00:30Z",
        user: {login: ("commenter-" + ($index | tostring))}
      }
  ]
  | . + [[{
  state: "CHANGES_REQUESTED",
  commit_id: "3333333333333333333333333333333333333333",
  submitted_at: "2026-07-14T18:01:00Z",
  user: {login: "reviewer"}
  }]]
' "$tmp/auth-reviews-approved.json" >"$tmp/api/auth-pr-$auth_pr.reviews.json"
install -d "$tmp/review-failure"
if PATH="$tmp/bin:$PATH" MOCK_ROOT="$tmp" "$verifier" \
  "$tmp/review-failure" "$appsell_sha" "$auth_sha" "$foundation_sha" \
  "$appsell_run" "$auth_run" "$foundation_run" "$auth_pr" "$auth_workflow_sha" >/dev/null 2>&1; then
  echo "Verifier accepted a current Auth changes-requested review" >&2
  exit 1
fi
cp "$tmp/auth-reviews-approved.json" "$tmp/api/auth-pr-$auth_pr.reviews.json"

cp "$tmp/api/auth-pr-$auth_pr.json" "$tmp/auth-pr-main.json"
jq '.base.ref = "stacked-feature"' "$tmp/auth-pr-main.json" \
  >"$tmp/api/auth-pr-$auth_pr.json"
install -d "$tmp/base-branch-failure"
if PATH="$tmp/bin:$PATH" MOCK_ROOT="$tmp" "$verifier" \
  "$tmp/base-branch-failure" "$appsell_sha" "$auth_sha" "$foundation_sha" \
  "$appsell_run" "$auth_run" "$foundation_run" "$auth_pr" "$auth_workflow_sha" >/dev/null 2>&1; then
  echo "Verifier accepted an Auth source PR not targeting main" >&2
  exit 1
fi
cp "$tmp/auth-pr-main.json" "$tmp/api/auth-pr-$auth_pr.json"

jq '.base.repo.full_name = "attacker/fork"' "$tmp/auth-pr-main.json" \
  >"$tmp/api/auth-pr-$auth_pr.json"
install -d "$tmp/base-repo-failure"
if PATH="$tmp/bin:$PATH" MOCK_ROOT="$tmp" "$verifier" \
  "$tmp/base-repo-failure" "$appsell_sha" "$auth_sha" "$foundation_sha" \
  "$appsell_run" "$auth_run" "$foundation_run" "$auth_pr" "$auth_workflow_sha" >/dev/null 2>&1; then
  echo "Verifier accepted an Auth source PR with a foreign base repository" >&2
  exit 1
fi
cp "$tmp/auth-pr-main.json" "$tmp/api/auth-pr-$auth_pr.json"

jq -n '[[{event: "base_ref_changed", created_at: "2026-07-14T18:00:30Z"}]]' \
  >"$tmp/api/auth-pr-$auth_pr.timeline.json"
install -d "$tmp/retarget-failure"
if PATH="$tmp/bin:$PATH" MOCK_ROOT="$tmp" "$verifier" \
  "$tmp/retarget-failure" "$appsell_sha" "$auth_sha" "$foundation_sha" \
  "$appsell_run" "$auth_run" "$foundation_run" "$auth_pr" "$auth_workflow_sha" >/dev/null 2>&1; then
  echo "Verifier accepted an Auth source PR retargeted after approval" >&2
  exit 1
fi
printf '[[]]\n' >"$tmp/api/auth-pr-$auth_pr.timeline.json"

appsell_artifacts="$tmp/api/autonom-ia/appsell/$appsell_run/artifacts.json"
cp "$appsell_artifacts" "$tmp/appsell-artifacts-original.json"
jq '.artifacts[0].digest = "sha256:0000000000000000000000000000000000000000000000000000000000000000"' \
  "$appsell_artifacts" \
  >"$tmp/api/autonom-ia/appsell/$appsell_run/artifacts-tampered.json"
mv "$tmp/api/autonom-ia/appsell/$appsell_run/artifacts-tampered.json" \
  "$appsell_artifacts"
install -d "$tmp/digest-failure"
if PATH="$tmp/bin:$PATH" MOCK_ROOT="$tmp" "$verifier" \
  "$tmp/digest-failure" "$appsell_sha" "$auth_sha" "$foundation_sha" \
  "$appsell_run" "$auth_run" "$foundation_run" "$auth_pr" "$auth_workflow_sha" >/dev/null 2>&1; then
  echo "Verifier accepted a tampered GitHub API digest" >&2
  exit 1
fi
cp "$tmp/appsell-artifacts-original.json" "$appsell_artifacts"

cp "$tmp/api/artifact-1002.zip" "$tmp/artifact-1002-original.zip"
printf 'unexpected member\n' >"$tmp/artifact-1002/extra.txt"
rm -f "$tmp/api/artifact-1002.zip"
(
  cd "$tmp/artifact-1002"
  zip -q "$tmp/api/artifact-1002.zip" ./*
)
zip_digest="$(sha256_file "$tmp/api/artifact-1002.zip")"
zip_size="$(wc -c <"$tmp/api/artifact-1002.zip" | tr -d ' ')"
jq --arg digest "sha256:$zip_digest" --argjson size "$zip_size" '
  (.artifacts[] | select(.id == 1002)).digest = $digest
  | (.artifacts[] | select(.id == 1002)).size_in_bytes = $size
' "$appsell_artifacts" >"$tmp/appsell-artifacts-extra.json"
mv "$tmp/appsell-artifacts-extra.json" "$appsell_artifacts"
install -d "$tmp/zip-tree-failure"
if PATH="$tmp/bin:$PATH" MOCK_ROOT="$tmp" "$verifier" \
  "$tmp/zip-tree-failure" "$appsell_sha" "$auth_sha" "$foundation_sha" \
  "$appsell_run" "$auth_run" "$foundation_run" "$auth_pr" "$auth_workflow_sha" >/dev/null 2>&1; then
  echo "Verifier accepted an unexpected ZIP member" >&2
  exit 1
fi
cp "$tmp/artifact-1002-original.zip" "$tmp/api/artifact-1002.zip"
cp "$tmp/appsell-artifacts-original.json" "$appsell_artifacts"

config_subject="$tmp/artifact-1001/config-$appsell_sha.tar.gz"
python3 - "$config_subject" <<'PY'
import io
import sys
import tarfile

with tarfile.open(sys.argv[1], "w:gz") as archive:
    payload = b"escape"
    member = tarfile.TarInfo("../escape")
    member.size = len(payload)
    archive.addfile(member, io.BytesIO(payload))
PY
config_digest="$(sha256_file "$config_subject")"
jq --arg digest "$config_digest" '.subject_sha256 = $digest' \
  "$tmp/artifact-1001/provenance.json" >"$tmp/artifact-1001/provenance-updated.json"
mv "$tmp/artifact-1001/provenance-updated.json" "$tmp/artifact-1001/provenance.json"
rm -f "$tmp/api/artifact-1001.zip"
(
  cd "$tmp/artifact-1001"
  zip -q "$tmp/api/artifact-1001.zip" ./*
)
zip_digest="$(sha256_file "$tmp/api/artifact-1001.zip")"
zip_size="$(wc -c <"$tmp/api/artifact-1001.zip" | tr -d ' ')"
jq --arg digest "sha256:$zip_digest" --argjson size "$zip_size" '
  (.artifacts[] | select(.id == 1001)).digest = $digest
  | (.artifacts[] | select(.id == 1001)).size_in_bytes = $size
' "$appsell_artifacts" >"$tmp/appsell-artifacts-traversal.json"
mv "$tmp/appsell-artifacts-traversal.json" "$appsell_artifacts"
install -d "$tmp/tar-traversal-failure"
if PATH="$tmp/bin:$PATH" MOCK_ROOT="$tmp" "$verifier" \
  "$tmp/tar-traversal-failure" "$appsell_sha" "$auth_sha" "$foundation_sha" \
  "$appsell_run" "$auth_run" "$foundation_run" "$auth_pr" "$auth_workflow_sha" >/dev/null 2>&1; then
  echo "Verifier accepted a config TAR traversal" >&2
  exit 1
fi

echo "GitHub artifact verifier fixtures: passed"
