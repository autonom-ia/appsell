#!/usr/bin/env bash
set -euo pipefail

repo="."
phase="all"

usage() {
  cat <<'EOF'
Uso: harness-ci.sh [--repo PATH] [--phase plan|core|gitleaks|osv|promptfoo|summary|all]

Entrypoint determinístico do job único do Autonom.ia Agent Harness.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      repo="${2:-}"
      shift 2
      ;;
    --phase)
      phase="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Opção desconhecida: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$phase" in
  plan|core|gitleaks|osv|promptfoo|summary|all) ;;
  *)
    echo "--phase inválida: $phase" >&2
    exit 2
    ;;
esac

repo="$(cd "$repo" && pwd)"
state_key="${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}"
state_dir="${HARNESS_CI_STATE_DIR:-${RUNNER_TEMP:-${TMPDIR:-/tmp}}/autonomia-harness-ci-${state_key}}"
results_file="$state_dir/results.tsv"
changed_file="$state_dir/changed-files.nul"
plan_file="$state_dir/plan.json"
started_file="$state_dir/started-at"
shellcheck_files="$state_dir/shellcheck-files.txt"
shellcheck_scope="changed"
mkdir -p "$state_dir"

manifest="$repo/platform-manifest.json"
if [[ ! -f "$manifest" ]] && [[ -f "$repo/repo-kit/platform-manifest.json" ]]; then
  manifest="$repo/repo-kit/platform-manifest.json"
fi

manifest_value() {
  local expression="$1" fallback="$2" value
  if [[ -f "$manifest" ]] && jq empty "$manifest" >/dev/null 2>&1; then
    value="$(jq -r "$expression" "$manifest" 2>/dev/null || true)"
    if [[ -n "$value" ]] && [[ "$value" != "null" ]]; then
      printf '%s\n' "$value"
      return
    fi
  fi
  printf '%s\n' "$fallback"
}

risk_profile="$(manifest_value '.risk_profile // "standard"' 'standard')"
case "$risk_profile" in
  lean|standard|critical) ;;
  *) risk_profile="standard" ;;
esac

record_result() {
  local check_name="$1" check_status="$2" duration="$3" detail="$4"
  printf '%s\t%s\t%s\t%s\n' "$check_name" "$check_status" "$duration" "$detail" >> "$results_file"
}

record_skip() {
  record_result "$1" "SKIPPED" "0" "$2"
}

run_check() {
  local check_name="$1" detail="$2" started duration status=0
  shift 2
  started="$(date +%s)"
  "$@" || status=$?
  duration=$(( $(date +%s) - started ))
  if [[ "$status" -eq 0 ]]; then
    record_result "$check_name" "PASSED" "$duration" "$detail"
    return 0
  fi
  record_result "$check_name" "FAILED" "$duration" "$detail"
  return 1
}

missing_tool() {
  local check_name="$1" tool="$2"
  if [[ "${HARNESS_CI_REQUIRE_TOOLS:-false}" == "true" ]] || [[ "$risk_profile" == "critical" ]]; then
    record_result "$check_name" "FAILED" "0" "tool_unavailable:${tool}"
    return 1
  fi
  record_skip "$check_name" "tool_unavailable:${tool}"
  return 0
}

is_true() {
  case "$1" in
    true|TRUE|1|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

is_lockfile() {
  printf '%s\n' "$1" | grep -Eq '(^|/)(package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|Gemfile\.lock|Cargo\.lock|go\.sum|poetry\.lock|Pipfile\.lock|uv\.lock|composer\.lock|mix\.lock)$'
}

has_package_sources() {
  git -C "$repo" ls-files | grep -Eq '(^|/)(package\.json|package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|Gemfile|Gemfile\.lock|Cargo\.toml|Cargo\.lock|go\.mod|go\.sum|pyproject\.toml|requirements[^/]*\.txt|setup\.py|setup\.cfg|poetry\.lock|Pipfile|Pipfile\.lock|uv\.lock|composer\.json|composer\.lock|mix\.exs|mix\.lock|pom\.xml|build\.gradle(\.kts)?|gradle\.lockfile|packages\.lock\.json|Directory\.Packages\.props|Package\.resolved|Podfile\.lock|pubspec\.lock|renv\.lock)$|\.(csproj|fsproj)$'
}

is_prompt_file() {
  printf '%s\n' "$1" | grep -Eqi '(^|/)(prompts?|agents?)/|(^|/)(AGENTS|CLAUDE|CURSOR|OPENCODE)\.md$|\.prompt\.|promptfooconfig\.(ya?ml|json)$'
}

find_prompt_config() {
  local candidate
  for candidate in promptfooconfig.yaml promptfooconfig.yml promptfooconfig.json .promptfoo/promptfooconfig.yaml .promptfoo/promptfooconfig.yml; do
    if [[ -f "$repo/$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

resolve_changed_files() {
  local base_sha head_sha changed
  : > "$changed_file"
  if [[ -n "${HARNESS_CI_CHANGED_FILES_FILE:-}" ]] && [[ -f "$HARNESS_CI_CHANGED_FILES_FILE" ]]; then
    # Backward-compatible test/local override. Git-derived paths below remain
    # NUL-delimited end to end, including filenames containing newlines.
    while IFS= read -r changed || [[ -n "$changed" ]]; do
      [[ -n "$changed" ]] || continue
      [[ "$changed" != /* ]] || continue
      [[ "$changed" != ".." && "$changed" != ../* && "$changed" != */../* && "$changed" != */.. ]] || continue
      printf '%s\0' "$changed" >> "$changed_file"
    done < "$HARNESS_CI_CHANGED_FILES_FILE"
    return
  fi

  base_sha="${HARNESS_CI_BASE_SHA:-${GITHUB_BASE_SHA:-}}"
  head_sha="${HARNESS_CI_HEAD_SHA:-${GITHUB_SHA:-HEAD}}"
  if [[ -n "$base_sha" ]] && git -C "$repo" cat-file -e "${base_sha}^{commit}" 2>/dev/null; then
    git -C "$repo" diff --name-only -z --diff-filter=ACMRTUXB "$base_sha" "$head_sha" > "$changed_file"
  elif git -C "$repo" rev-parse HEAD^ >/dev/null 2>&1; then
    git -C "$repo" diff --name-only -z --diff-filter=ACMRTUXB HEAD^ HEAD > "$changed_file"
  else
    git -C "$repo" ls-files -z > "$changed_file"
  fi
}

phase_plan() {
  local weekly="false" lock_changed="false" prompt_changed="false" prompt_config=""
  local dependency_mode secret_mode prompt_on_change weekly_enabled osv_scan="false" prompt_eval="false" gitleaks_full="false"

  : > "$results_file"
  date +%s > "$started_file"
  resolve_changed_files

  if [[ "${GITHUB_EVENT_NAME:-}" == "schedule" ]] || is_true "${HARNESS_CI_WEEKLY:-false}"; then
    weekly="true"
  fi
  while IFS= read -r -d '' changed; do
    [[ -n "$changed" ]] || continue
    if is_lockfile "$changed"; then lock_changed="true"; fi
    if is_prompt_file "$changed"; then prompt_changed="true"; fi
  done < "$changed_file"
  prompt_config="$(find_prompt_config || true)"

  dependency_mode="$(manifest_value '.ci.dependency_scan // "changed"' 'changed')"
  secret_mode="$(manifest_value '.ci.secret_scan // "diff"' 'diff')"
  prompt_on_change="$(manifest_value '.ci.prompt_eval.on_change' 'true')"
  weekly_enabled="$(manifest_value '.ci.weekly_full_scan' 'true')"

  if [[ "$dependency_mode" == "full" ]] || { [[ "$dependency_mode" == "changed" ]] && [[ "$lock_changed" == "true" ]]; }; then
    osv_scan="true"
  fi
  if [[ "$weekly" == "true" ]] && [[ "$weekly_enabled" == "true" ]] && [[ "$dependency_mode" != "off" ]]; then
    osv_scan="true"
  fi
  if [[ "$prompt_on_change" == "true" ]] && [[ "$prompt_changed" == "true" ]] && [[ -n "$prompt_config" ]] && [[ "$weekly" != "true" ]]; then
    prompt_eval="true"
  fi
  if [[ "$weekly" == "true" ]] && [[ "$weekly_enabled" == "true" ]] && [[ "$secret_mode" != "off" ]]; then
    gitleaks_full="true"
  elif [[ "$secret_mode" == "full" ]]; then
    gitleaks_full="true"
  fi

  jq -n \
    --argjson weekly "$weekly" \
    --argjson lock_changed "$lock_changed" \
    --argjson prompt_changed "$prompt_changed" \
    --argjson osv_scan "$osv_scan" \
    --argjson prompt_eval "$prompt_eval" \
    --argjson gitleaks_full "$gitleaks_full" \
    --arg prompt_config "$prompt_config" \
    '{weekly:$weekly, lockfiles_changed:$lock_changed, prompt_files_changed:$prompt_changed, osv_scan:$osv_scan, prompt_eval:$prompt_eval, gitleaks_full:$gitleaks_full, prompt_config:$prompt_config}' \
    | tee "$plan_file"

  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    {
      printf 'osv_scan=%s\n' "$osv_scan"
      printf 'prompt_eval=%s\n' "$prompt_eval"
      printf 'gitleaks_full=%s\n' "$gitleaks_full"
    } >> "$GITHUB_OUTPUT"
  fi
}

shell_syntax_check() {
  git -C "$repo" ls-files -z -- '*.sh' | (cd "$repo" && xargs -0 -n1 bash -n)
}

prepare_shellcheck_files() {
  local changed
  ensure_plan
  : > "$shellcheck_files"
  shellcheck_scope="changed"
  if [[ "$(jq -r '.weekly' "$plan_file")" == "true" ]] && \
    [[ "$(manifest_value '.ci.weekly_full_scan' 'true')" == "true" ]]; then
    shellcheck_scope="full"
    git -C "$repo" ls-files -z -- '*.sh' > "$shellcheck_files"
    return 0
  fi

  while IFS= read -r -d '' changed; do
    [[ "$changed" == *.sh ]] || continue
    [[ -f "$repo/$changed" ]] || continue
    git -C "$repo" ls-files --error-unmatch -- "$changed" >/dev/null 2>&1 || continue
    printf '%s\0' "$changed" >> "$shellcheck_files"
  done < "$changed_file"
}

shellcheck_check() {
  (cd "$repo" && xargs -0 shellcheck -- < "$shellcheck_files")
}

bats_check() {
  local bats_list="$state_dir/bats-files.txt"
  find "$repo" -path "$repo/.git" -prune -o -type f -name '*.bats' -print > "$bats_list"
  if [[ ! -s "$bats_list" ]]; then
    return 125
  fi
  while IFS= read -r bats_file; do
    env \
      -u HARNESS_CI_WEEKLY \
      -u HARNESS_CI_STATE_DIR \
      -u HARNESS_CI_CHANGED_FILES_FILE \
      -u HARNESS_CI_BASE_SHA \
      -u HARNESS_CI_HEAD_SHA \
      -u HARNESS_CI_REQUIRE_TOOLS \
      -u HARNESS_LIVE_EVAL \
      -u HARNESS_EVAL_BUDGET_USD \
      -u GITHUB_EVENT_NAME \
      -u GITHUB_BASE_SHA \
      -u GITHUB_SHA \
      -u GITHUB_OUTPUT \
      -u GITHUB_STEP_SUMMARY \
      bats "$bats_file"
  done < "$bats_list"
}

doctor_check() {
  local doctor_script="$repo/scripts/harness-doctor.sh" doctor_json="$state_dir/doctor.json"
  if [[ ! -x "$doctor_script" ]] && [[ -x "$repo/repo-kit/scripts/harness-doctor.sh" ]]; then
    doctor_script="$repo/repo-kit/scripts/harness-doctor.sh"
  fi
  [[ -x "$doctor_script" ]] || return 125
  bash "$doctor_script" --json "$repo" "$repo" > "$doctor_json"
  jq -e '.schema_version and .summary and .layers' "$doctor_json" >/dev/null
}

validator_check() {
  [[ -x "$repo/scripts/validate-harness.sh" ]] || return 125
  (cd "$repo" && bash scripts/validate-harness.sh)
}

actionlint_check() {
  local workflow found="false"
  while IFS= read -r workflow; do
    found="true"
    actionlint "$workflow"
  done < <(find "$repo" -path "$repo/.git" -prune -o -type f -path '*/.github/workflows/*.yml' -print)
  [[ "$found" == "true" ]]
}

zizmor_check() {
  local workflow found="false"
  while IFS= read -r workflow; do
    found="true"
    zizmor --offline "$workflow"
  done < <(find "$repo" -path "$repo/.git" -prune -o -type f -path '*/.github/workflows/*.yml' -print)
  [[ "$found" == "true" ]]
}

phase_core() {
  local failed=0 bats_status=0

  run_check "bash-n" "tracked_shell_files" shell_syntax_check || failed=1

  if [[ "$(manifest_value '.ci.shellcheck' 'true')" != "true" ]]; then
    record_skip "shellcheck" "disabled_by_manifest"
  elif ! command -v shellcheck >/dev/null 2>&1; then
    missing_tool "shellcheck" "shellcheck" || failed=1
  else
    prepare_shellcheck_files
    if [[ ! -s "$shellcheck_files" ]]; then
      record_skip "shellcheck" "no_changed_shell_files"
    else
      run_check "shellcheck" "${shellcheck_scope}_shell_files" shellcheck_check || failed=1
    fi
  fi

  if [[ "$(manifest_value '.ci.bats' 'true')" != "true" ]]; then
    record_skip "bats" "disabled_by_manifest"
  elif ! command -v bats >/dev/null 2>&1; then
    missing_tool "bats" "bats" || failed=1
  else
    bats_check || bats_status=$?
    case "$bats_status" in
      0) record_result "bats" "PASSED" "0" "repository_bats_suite" ;;
      125) record_skip "bats" "no_bats_tests" ;;
      *) record_result "bats" "FAILED" "0" "repository_bats_suite"; failed=1 ;;
    esac
  fi

  if [[ "$(manifest_value '.ci.workflow_lint' 'true')" != "true" ]]; then
    record_skip "actionlint" "disabled_by_manifest"
    record_skip "zizmor" "disabled_by_manifest"
  else
    if ! command -v actionlint >/dev/null 2>&1; then
      missing_tool "actionlint" "actionlint" || failed=1
    else
      run_check "actionlint" "github_workflows" actionlint_check || failed=1
    fi
    if ! command -v zizmor >/dev/null 2>&1; then
      missing_tool "zizmor" "zizmor" || failed=1
    else
      run_check "zizmor" "offline_workflow_audit" zizmor_check || failed=1
    fi
  fi

  if doctor_check; then
    record_result "harness-doctor-json" "PASSED" "0" "sanitized_json"
  else
    case "$?" in
      125) record_skip "harness-doctor-json" "doctor_unavailable" ;;
      *) record_result "harness-doctor-json" "FAILED" "0" "invalid_or_degraded"; failed=1 ;;
    esac
  fi

  if validator_check; then
    record_result "validate-harness" "PASSED" "0" "installed_harness_contract"
  else
    case "$?" in
      125) record_skip "validate-harness" "validator_unavailable" ;;
      *) record_result "validate-harness" "FAILED" "0" "installed_harness_contract"; failed=1 ;;
    esac
  fi

  return "$failed"
}

ensure_plan() {
  if [[ ! -f "$plan_file" ]]; then
    phase_plan >/dev/null
  fi
}

phase_gitleaks() {
  local wrapper="$repo/scripts/gitleaks-redacted.sh" scan_mode base_sha head_sha
  ensure_plan
  if [[ "$(manifest_value '.ci.secret_scan // "diff"' 'diff')" == "off" ]]; then
    record_skip "gitleaks" "disabled_by_manifest"
    return 0
  fi
  if [[ ! -x "$wrapper" ]]; then
    record_result "gitleaks" "FAILED" "0" "wrapper_unavailable"
    return 1
  fi
  if ! command -v gitleaks >/dev/null 2>&1; then
    missing_tool "gitleaks" "gitleaks"
    return $?
  fi

  scan_mode="diff"
  if [[ "$(jq -r '.gitleaks_full' "$plan_file")" == "true" ]]; then scan_mode="full"; fi
  base_sha="${HARNESS_CI_BASE_SHA:-${GITHUB_BASE_SHA:-}}"
  head_sha="${HARNESS_CI_HEAD_SHA:-${GITHUB_SHA:-HEAD}}"
  if [[ "$scan_mode" == "diff" ]] && { [[ -z "$base_sha" ]] || ! git -C "$repo" cat-file -e "${base_sha}^{commit}" 2>/dev/null; } && git -C "$repo" rev-parse HEAD^ >/dev/null 2>&1; then
    base_sha="$(git -C "$repo" rev-parse HEAD^)"
  fi
  if [[ "$scan_mode" == "diff" ]] && [[ -z "$base_sha" ]]; then
    scan_mode="full"
  fi

  if GITLEAKS_MODE="$scan_mode" GITLEAKS_BASE_SHA="$base_sha" GITLEAKS_HEAD_SHA="$head_sha" \
    run_check "gitleaks" "${scan_mode}_redacted" bash "$wrapper" "$repo"; then
    return 0
  fi
  return 1
}

phase_osv() {
  local failed=0 raw_output="$state_dir/osv-output.txt" lockfile
  ensure_plan
  if [[ "$(jq -r '.osv_scan' "$plan_file")" != "true" ]]; then
    record_skip "osv-scanner" "no_changed_lockfiles"
    return 0
  fi
  if ! has_package_sources; then
    record_skip "osv-scanner" "no_package_sources"
    return 0
  fi
  if ! command -v osv-scanner >/dev/null 2>&1; then
    missing_tool "osv-scanner" "osv-scanner"
    return $?
  fi

  if [[ "$(jq -r '.weekly' "$plan_file")" == "true" ]] || [[ "$(manifest_value '.ci.dependency_scan // "changed"' 'changed')" == "full" ]]; then
    (cd "$repo" && osv-scanner scan source --recursive .) > "$raw_output" 2>&1 || failed=1
  else
    while IFS= read -r -d '' lockfile; do
      is_lockfile "$lockfile" || continue
      [[ -f "$repo/$lockfile" ]] || continue
      (cd "$repo" && osv-scanner scan -L "$lockfile") > "$raw_output" 2>&1 || failed=1
    done < "$changed_file"
  fi
  rm -f "$raw_output"
  if [[ "$failed" -eq 0 ]]; then
    record_result "osv-scanner" "PASSED" "0" "dependency_scan"
    return 0
  fi
  record_result "osv-scanner" "FAILED" "0" "vulnerabilities_or_tool_error"
  return 1
}

positive_budget() {
  awk -v value="$1" 'BEGIN { exit ! (value ~ /^[0-9]+([.][0-9]+)?$/ && value + 0 > 0) }'
}

phase_promptfoo() {
  local config live_enabled budget raw_output="$state_dir/promptfoo-output.txt"
  ensure_plan
  if [[ "$(jq -r '.prompt_eval' "$plan_file")" != "true" ]]; then
    record_skip "promptfoo" "no_changed_prompt_or_config"
    return 0
  fi
  config="$(jq -r '.prompt_config' "$plan_file")"
  live_enabled="$(manifest_value '.ci.prompt_eval.live_providers' 'false')"
  budget="$(manifest_value '.ci.prompt_eval.budget_usd // 0' '0')"
  if ! is_true "${HARNESS_LIVE_EVAL:-false}" || [[ "$live_enabled" != "true" ]] || ! positive_budget "$budget"; then
    record_skip "promptfoo" "live_eval_not_authorized"
    return 0
  fi
  if ! command -v promptfoo >/dev/null 2>&1; then
    missing_tool "promptfoo" "promptfoo"
    return $?
  fi
  if (cd "$repo" && PROMPTFOO_DISABLE_TELEMETRY=1 PROMPTFOO_CACHE_ENABLED=false \
    promptfoo eval --config "$config" --no-cache) > "$raw_output" 2>&1; then
    rm -f "$raw_output"
    record_result "promptfoo" "PASSED" "0" "changed_prompts_only"
    return 0
  fi
  rm -f "$raw_output"
  record_result "promptfoo" "FAILED" "0" "evaluation_failed"
  return 1
}

phase_summary() {
  local version="unknown" started now duration passed failed skipped result summary_target
  if [[ -f "$manifest" ]]; then version="$(manifest_value '.version // "unknown"' 'unknown')"; fi
  if [[ "$version" == "unknown" ]] && [[ -f "$repo/VERSION" ]]; then version="$(tr -d '[:space:]' < "$repo/VERSION")"; fi
  if [[ ! -f "$started_file" ]]; then date +%s > "$started_file"; fi
  if [[ ! -f "$results_file" ]]; then : > "$results_file"; fi
  if is_true "${HARNESS_CI_EXTERNAL_FAILURE:-false}" && ! grep -Fq $'workflow-runtime\tFAILED' "$results_file"; then
    record_result "workflow-runtime" "FAILED" "0" "setup_or_plan_failed"
  fi
  started="$(cat "$started_file")"
  now="$(date +%s)"
  duration=$((now - started))
  passed="$(awk -F '\t' '$2 == "PASSED" {count++} END {print count+0}' "$results_file")"
  failed="$(awk -F '\t' '$2 == "FAILED" {count++} END {print count+0}' "$results_file")"
  skipped="$(awk -F '\t' '$2 == "SKIPPED" {count++} END {print count+0}' "$results_file")"
  result="PASS"
  if [[ "$failed" -gt 0 ]]; then result="FAIL"; fi
  summary_target="${GITHUB_STEP_SUMMARY:-/dev/stdout}"

  {
    echo "## Harness ${version}"
    echo
    echo "| Field | Value |"
    echo "|---|---|"
    echo "| Risk profile | ${risk_profile} |"
    echo "| Checks run/skipped | $((passed + failed)) run / ${skipped} skipped |"
    echo "| Duration | ${duration}s |"
    echo "| Result | ${result} |"
    echo
    echo "Live-eval budget is authorization only; not a hard cap."
    echo
    echo "### Checks"
    echo
    echo "| Check | Status | Duration | Note |"
    echo "|---|---|---:|---|"
    awk -F '\t' '{printf "| %s | %s | %ss | %s |\n", $1, $2, $3, $4}' "$results_file"
  } >> "$summary_target"

  if [[ "$failed" -gt 0 ]]; then
    artifact_dir="${HARNESS_CI_ARTIFACT_DIR:-$repo/.harness-ci-artifacts}"
    mkdir -p "$artifact_dir"
    {
      echo "Harness CI failed."
      echo "Failed checks: $failed"
      echo "No prompts, findings, paths, tokens or customer data are retained."
    } > "$artifact_dir/harness-ci-failure.txt"
    return 1
  fi
  return 0
}

case "$phase" in
  plan) phase_plan ;;
  core) phase_core ;;
  gitleaks) phase_gitleaks ;;
  osv) phase_osv ;;
  promptfoo) phase_promptfoo ;;
  summary) phase_summary ;;
  all)
    overall=0
    phase_plan >/dev/null || overall=1
    phase_core || overall=1
    phase_gitleaks || overall=1
    phase_osv || overall=1
    phase_promptfoo || overall=1
    phase_summary || overall=1
    exit "$overall"
    ;;
esac
