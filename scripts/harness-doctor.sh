#!/usr/bin/env bash
# Harness Doctor v2.2 — layer-aware health check with sanitized JSON output.
set -uo pipefail

json_mode="false"
if [[ "${1:-}" == "--json" ]]; then
  json_mode="true"
  shift
fi

TARGET="${1:-.}"
SOURCE_ROOT="${2:-$HOME/Documents/agent-harness}"
REPORT_DIR="$TARGET/docs/harness-health"
TIMESTAMP="$(date +"%Y%m%d-%H%M%S")"
REPORT="$REPORT_DIR/doctor-${TIMESTAMP}.md"
findings_file="$(mktemp)"
trap 'rm -f "$findings_file"' EXIT

degraded=0
warnings=0
l1_errors=0
l1_warnings=0
l2_errors=0
l2_warnings=0
l3_errors=0
l3_warnings=0
l4_errors=0
l4_warnings=0

emit() {
  if [[ "$json_mode" != "true" ]]; then
    printf '%s\n' "$*"
  fi
}

increment_layer() {
  local layer="$1" severity="$2"
  case "${layer}:${severity}" in
    L1:ERROR) l1_errors=$((l1_errors + 1)) ;;
    L1:WARN) l1_warnings=$((l1_warnings + 1)) ;;
    L2:ERROR) l2_errors=$((l2_errors + 1)) ;;
    L2:WARN) l2_warnings=$((l2_warnings + 1)) ;;
    L3:ERROR) l3_errors=$((l3_errors + 1)) ;;
    L3:WARN) l3_warnings=$((l3_warnings + 1)) ;;
    L4:ERROR) l4_errors=$((l4_errors + 1)) ;;
    L4:WARN) l4_warnings=$((l4_warnings + 1)) ;;
  esac
}

record_finding() {
  local severity="$1" layer="$2" code="$3" file="$4" message="$5"
  if [[ "$severity" == "ERROR" ]]; then
    degraded=$((degraded + 1))
  else
    warnings=$((warnings + 1))
  fi
  increment_layer "$layer" "$severity"
  emit "[$severity][$layer] $code: $file"
  jq -nc \
    --arg severity "$severity" \
    --arg layer "$layer" \
    --arg code "$code" \
    --arg file "$file" \
    --arg message "$message" \
    '{severity:$severity,layer:$layer,code:$code,file:$file,message:$message}' >> "$findings_file"
}

check() {
  local layer="$1" file="$2" severity="${3:-ERROR}"
  if [[ ! -f "$TARGET/$file" ]]; then
    record_finding "$severity" "$layer" "MISSING_FILE" "$file" "Required Harness file is missing"
  fi
}

check_content() {
  local layer="$1" file="$2" pattern="$3" severity="${4:-WARN}"
  if [[ -f "$TARGET/$file" ]] && ! grep -qi "$pattern" "$TARGET/$file"; then
    record_finding "$severity" "$layer" "MISSING_SECTION" "$file" "Required policy section is missing"
  fi
}

emit "=== Autonom.ia Harness Doctor v2.2 ==="
emit "$(date -u +"%Y-%m-%d %H:%M UTC")"
emit

emit "--- Layer 1: Core ---"
check "L1" "AGENTS.md" "ERROR"
check "L1" "CLAUDE.md" "ERROR"
check "L1" "platform-manifest.json" "WARN"
check_content "L1" "AGENTS.md" "Model Routing" "WARN"
check_content "L1" "AGENTS.md" "Audit Trail" "WARN"
check_content "L1" "AGENTS.md" "Environment" "WARN"
check "L1" ".claude/rules/git.md" "WARN"
check "L1" ".claude/rules/prod-approval.md" "WARN"
check "L1" ".claude/rules/environment.md" "WARN"

emit "--- Layer 2: Capabilities ---"
for skill in systematic-debugging pr-review rollback-planning memory-compaction issue-shaping spec-writing project-update harness-install-or-update incident-response security-review model-routing; do
  check "L2" "skills/$skill/SKILL.md" "WARN"
done
for agent in implementer reviewer planner memory-curator release-checker security-reviewer tester ops-agent; do
  check "L2" ".claude/agents/$agent.md" "WARN"
done

emit "--- Layer 3: Infra ---"
check "L3" ".claude/hooks/pre-tool-use.sh" "WARN"
check "L3" ".claude/hooks/post-tool-use.sh" "WARN"
check "L3" ".claude/settings.json" "WARN"
check "L3" ".github/workflows/agent-harness-check.yml" "WARN"
check "L3" "scripts/validate-harness.sh" "WARN"
check "L3" "scripts/harness-doctor.sh" "WARN"
check "L3" "scripts/harness-ci.sh" "WARN"

for hook in pre-tool-use.sh post-tool-use.sh; do
  hook_path="$TARGET/.claude/hooks/$hook"
  if [[ -f "$hook_path" ]] && ! head -1 "$hook_path" | grep -q "#!/"; then
    record_finding "WARN" "L3" "INVALID_SHEBANG" ".claude/hooks/$hook" "Hook does not start with a shell shebang"
  fi
done

emit "--- Layer 4: Observatory ---"
for mem in MEMORY_CORE MEMORY_SESSION MEMORY_PROJECT MEMORY_USER MEMORY_LEARNINGS MEMORY_DECISIONS MEMORY_COMPACTION MEMORY_INCIDENTS MEMORY_AGENTS; do
  check "L4" "docs/memory/$mem.md" "WARN"
done
check "L4" "docs/audit/SESSION_TEMPLATE.md" "WARN"
check "L4" "docs/STATUS.md" "WARN"

emit "--- Version ---"
installed_version="unknown"
installed_mode="warn"
risk_profile="standard"
if [[ -f "$TARGET/platform-manifest.json" ]]; then
  installed_version="$(jq -r '.version // "unknown"' "$TARGET/platform-manifest.json" 2>/dev/null || echo "parse-error")"
  installed_mode="$(jq -r '.enforcement_mode // "warn"' "$TARGET/platform-manifest.json" 2>/dev/null || echo "warn")"
  risk_profile="$(jq -r '.risk_profile // "standard"' "$TARGET/platform-manifest.json" 2>/dev/null || echo "standard")"
  emit "Installed: $installed_version (mode=$installed_mode, risk=$risk_profile)"
  if [[ -f "$SOURCE_ROOT/VERSION" ]]; then
    available="$(tr -d '[:space:]' < "$SOURCE_ROOT/VERSION")"
    emit "Available: $available"
    if [[ "$installed_version" != "$available" ]]; then
      record_finding "WARN" "L1" "VERSION_DRIFT" "VERSION" "Installed and available Harness versions differ"
    fi
  else
    record_finding "WARN" "L1" "SOURCE_VERSION_UNAVAILABLE" "VERSION" "Source version is unavailable; drift check skipped"
  fi
elif [[ -f "$TARGET/.autonomia-harness.json" ]]; then
  installed_version="$(jq -r '.version // "unknown"' "$TARGET/.autonomia-harness.json" 2>/dev/null || echo "unknown")"
  record_finding "WARN" "L1" "LEGACY_MANIFEST" ".autonomia-harness.json" "Legacy manifest detected; update to platform-manifest.json"
else
  record_finding "WARN" "L1" "MISSING_MANIFEST" "platform-manifest.json" "No Harness manifest found"
fi

status="OK"
if [[ "$degraded" -gt 0 ]]; then
  status="DEGRADED"
elif [[ "$warnings" -gt 0 ]]; then
  status="WARN"
fi

layer_status() {
  local errors="$1" layer_warnings="$2"
  if [[ "$errors" -gt 0 ]]; then
    printf 'DEGRADED'
  elif [[ "$layer_warnings" -gt 0 ]]; then
    printf 'WARN'
  else
    printf 'OK'
  fi
}

if [[ "$json_mode" == "true" ]]; then
  findings_json="$(jq -s '.' "$findings_file")"
  jq -n \
    --arg generated_at "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
    --arg version "$installed_version" \
    --arg mode "$installed_mode" \
    --arg risk "$risk_profile" \
    --arg status "$status" \
    --arg l1_status "$(layer_status "$l1_errors" "$l1_warnings")" \
    --arg l2_status "$(layer_status "$l2_errors" "$l2_warnings")" \
    --arg l3_status "$(layer_status "$l3_errors" "$l3_warnings")" \
    --arg l4_status "$(layer_status "$l4_errors" "$l4_warnings")" \
    --argjson errors "$degraded" \
    --argjson warnings "$warnings" \
    --argjson l1_errors "$l1_errors" --argjson l1_warnings "$l1_warnings" \
    --argjson l2_errors "$l2_errors" --argjson l2_warnings "$l2_warnings" \
    --argjson l3_errors "$l3_errors" --argjson l3_warnings "$l3_warnings" \
    --argjson l4_errors "$l4_errors" --argjson l4_warnings "$l4_warnings" \
    --argjson findings "$findings_json" '
      {
        schema_version: "1.0",
        generated_at: $generated_at,
        harness: {version:$version,enforcement_mode:$mode,risk_profile:$risk},
        summary: {status:$status,errors:$errors,warnings:$warnings},
        layers: [
          {id:"L1",name:"Core",status:$l1_status,errors:$l1_errors,warnings:$l1_warnings},
          {id:"L2",name:"Capabilities",status:$l2_status,errors:$l2_errors,warnings:$l2_warnings},
          {id:"L3",name:"Infra",status:$l3_status,errors:$l3_errors,warnings:$l3_warnings},
          {id:"L4",name:"Observatory",status:$l4_status,errors:$l4_errors,warnings:$l4_warnings}
        ],
        findings: $findings
      }
    '
else
  emit
  emit "=== Summary ==="
  emit "Errors (DEGRADED): $degraded"
  emit "Warnings: $warnings"

  mkdir -p "$REPORT_DIR" 2>/dev/null || true
  if [[ -w "$REPORT_DIR" ]] 2>/dev/null || mkdir -p "$REPORT_DIR" 2>/dev/null; then
    if {
      echo "# Harness Health Report $(date -u +"%Y-%m-%d %H:%M UTC")"
      echo
      echo "Installed: $installed_version (mode=$installed_mode, risk=$risk_profile)"
      echo "Errors: $degraded | Warnings: $warnings"
      echo "Status: $status"
      echo
      echo "## Findings"
      if [[ -s "$findings_file" ]]; then
        jq -r '"[\(.severity)][\(.layer)] \(.code): \(.file)"' "$findings_file"
      else
        echo "- None."
      fi
      echo
      echo "## Layer status"
      echo "- Layer 1 (Core): $(layer_status "$l1_errors" "$l1_warnings")"
      echo "- Layer 2 (Capabilities): $(layer_status "$l2_errors" "$l2_warnings")"
      echo "- Layer 3 (Infra): $(layer_status "$l3_errors" "$l3_warnings")"
      echo "- Layer 4 (Observatory): $(layer_status "$l4_errors" "$l4_warnings")"
      echo
      echo "## Next action"
      if [[ "$degraded" -gt 0 ]]; then
        echo "Rodar \`apply-harness-to-repo.sh\` para reinstalar arquivos faltantes."
      elif [[ "$warnings" -gt 0 ]]; then
        echo "Revisar warnings acima; considerar instalar componentes opcionais."
      else
        echo "Sem ação necessária. Harness saudável."
      fi
    } > "$REPORT" 2>/dev/null; then
      emit "Report: $REPORT"
    else
      emit "[WARN] relatório não salvo"
    fi
  else
    emit "[WARN] diretório de relatório não é gravável"
  fi
fi

if [[ "$installed_mode" == "strict" ]] && [[ "$degraded" -gt 0 ]]; then
  exit 1
fi
exit 0
