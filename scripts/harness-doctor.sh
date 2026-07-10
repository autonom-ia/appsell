#!/usr/bin/env bash
# harness-doctor.sh v2 — health check com layer-aware report + drift detection.
# Default: exit 0 (warn-only). Em strict mode: exit 1 se ERRORs encontrados.
# Output: docs/harness-health/doctor-YYYY-MM-DD-HHMMSS.md
set -uo pipefail

echo "=== Autonom.ia Harness Doctor v2 ==="
echo "$(date -u +"%Y-%m-%d %H:%M UTC")"
echo

TARGET="${1:-.}"
SOURCE_ROOT="${2:-$HOME/Documents/agent-harness}"
REPORT_DIR="$TARGET/docs/harness-health"
TIMESTAMP=$(date +"%Y%m%d-%H%M%S")
REPORT="$REPORT_DIR/doctor-${TIMESTAMP}.md"

degraded=0
warnings=0

check() {
  local layer="$1" file="$2" severity="${3:-ERROR}"
  if [ ! -f "$TARGET/$file" ]; then
    echo "[$severity][$layer] MISSING: $file"
    if [ "$severity" = "ERROR" ]; then
      degraded=$((degraded+1))
    else
      warnings=$((warnings+1))
    fi
  fi
}

check_content() {
  local layer="$1" file="$2" pattern="$3" severity="${4:-WARN}"
  if [ -f "$TARGET/$file" ] && ! grep -qi "$pattern" "$TARGET/$file"; then
    echo "[$severity][$layer] MISSING SECTION '$pattern' in $file"
    if [ "$severity" = "ERROR" ]; then
      degraded=$((degraded+1))
    else
      warnings=$((warnings+1))
    fi
  fi
}

echo "--- Layer 1: Core ---"
check "L1" "AGENTS.md" "ERROR"
check "L1" "CLAUDE.md" "ERROR"
check "L1" "platform-manifest.json" "WARN"
check_content "L1" "AGENTS.md" "Model Routing" "WARN"
check_content "L1" "AGENTS.md" "Audit Trail" "WARN"
check_content "L1" "AGENTS.md" "Environment" "WARN"
check "L1" ".claude/rules/git.md" "WARN"
check "L1" ".claude/rules/prod-approval.md" "WARN"
check "L1" ".claude/rules/environment.md" "WARN"

echo "--- Layer 2: Capabilities ---"
for skill in systematic-debugging pr-review rollback-planning memory-compaction issue-shaping spec-writing project-update harness-install-or-update incident-response security-review model-routing; do
  check "L2" "skills/$skill/SKILL.md" "WARN"
done
for agent in implementer reviewer planner memory-curator release-checker security-reviewer tester ops-agent; do
  check "L2" ".claude/agents/$agent.md" "WARN"
done

echo "--- Layer 3: Infra ---"
check "L3" ".claude/hooks/pre-tool-use.sh" "WARN"
check "L3" ".claude/hooks/post-tool-use.sh" "WARN"
check "L3" ".claude/settings.json" "WARN"
check "L3" ".github/workflows/agent-harness-check.yml" "WARN"
check "L3" "scripts/validate-harness.sh" "WARN"
check "L3" "scripts/harness-doctor.sh" "WARN"

# Verify hook scripts are real (not just README) — checks both pre and post
for hook in pre-tool-use.sh post-tool-use.sh; do
  hook_path="$TARGET/.claude/hooks/$hook"
  if [ -f "$hook_path" ]; then
    if ! head -1 "$hook_path" | grep -q "#!/"; then
      echo "[WARN][L3] $hook não é script shell válido (sem shebang)"
      warnings=$((warnings+1))
    fi
  fi
done

echo "--- Layer 4: Observatory ---"
for mem in MEMORY_CORE MEMORY_SESSION MEMORY_PROJECT MEMORY_USER MEMORY_LEARNINGS MEMORY_DECISIONS MEMORY_COMPACTION MEMORY_INCIDENTS MEMORY_AGENTS; do
  check "L4" "docs/memory/$mem.md" "WARN"
done
check "L4" "docs/audit/SESSION_TEMPLATE.md" "WARN"
check "L4" "docs/STATUS.md" "WARN"

# Version check + drift detection
echo "--- Version ---"
installed_version="unknown"
installed_mode="warn"
if [ -f "$TARGET/platform-manifest.json" ]; then
  installed_version=$(jq -r '.version // "unknown"' "$TARGET/platform-manifest.json" 2>/dev/null || echo "parse-error")
  installed_mode=$(jq -r '.enforcement_mode // "warn"' "$TARGET/platform-manifest.json" 2>/dev/null || echo "warn")
  echo "Installed: $installed_version (mode=$installed_mode)"
  if [ -f "$SOURCE_ROOT/VERSION" ]; then
    available=$(tr -d '[:space:]' < "$SOURCE_ROOT/VERSION")
    echo "Available: $available"
    if [ "$installed_version" != "$available" ]; then
      echo "[WARN][VERSION] Drift detected: installed=$installed_version vs available=$available"
      warnings=$((warnings+1))
    fi
  else
    echo "[WARN][VERSION] Source VERSION not found at $SOURCE_ROOT/VERSION — skip drift check"
  fi
elif [ -f "$TARGET/.autonomia-harness.json" ]; then
  v1_version=$(jq -r '.version // "unknown"' "$TARGET/.autonomia-harness.json" 2>/dev/null || echo "unknown")
  echo "[WARN][VERSION] Legacy v1 manifest detected (.autonomia-harness.json v$v1_version) — consider upgrading to v2"
  warnings=$((warnings+1))
else
  echo "[WARN][VERSION] No harness manifest found"
  warnings=$((warnings+1))
fi

echo
echo "=== Summary ==="
echo "Errors (DEGRADED): $degraded"
echo "Warnings: $warnings"

# Write report
mkdir -p "$REPORT_DIR" 2>/dev/null || true
if [ -w "$REPORT_DIR" ] 2>/dev/null || mkdir -p "$REPORT_DIR" 2>/dev/null; then
  {
    echo "# Harness Health Report $(date -u +"%Y-%m-%d %H:%M UTC")"
    echo
    echo "Installed: $installed_version (mode=$installed_mode)"
    echo "Errors: $degraded | Warnings: $warnings"
    echo "Status: $([ $degraded -eq 0 ] && echo 'OK' || echo 'DEGRADED')"
    echo
    echo "## Layer status"
    echo "- Layer 1 (Core): see summary above"
    echo "- Layer 2 (Capabilities): see summary above"
    echo "- Layer 3 (Infra): see summary above"
    echo "- Layer 4 (Observatory): see summary above"
    echo
    echo "## Next action"
    if [ $degraded -gt 0 ]; then
      echo "Rodar \`apply-harness-to-repo.sh\` para reinstalar arquivos faltantes."
    elif [ $warnings -gt 0 ]; then
      echo "Revisar warnings acima; considerar instalar componentes opcionais."
    else
      echo "Sem ação necessária. Harness saudável."
    fi
  } > "$REPORT" 2>/dev/null && echo "Report: $REPORT" || echo "[WARN] Não consegui escrever $REPORT"
else
  echo "[WARN] $REPORT_DIR não é gravável — report não salvo"
fi

# Read enforcement_mode — only exit 1 in strict mode
if [ "$installed_mode" = "strict" ] && [ $degraded -gt 0 ]; then
  echo "FAIL (strict mode): $degraded erros encontrados"
  exit 1
fi
exit 0
