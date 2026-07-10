#!/usr/bin/env bash
# validate-harness.sh v2 — reports harness completeness.
# Default: exit 0 (warn-only) — never blocks unless enforcement_mode=strict.
# In strict mode: exits 1 if Layer 1 (Core) is missing.
set -uo pipefail

echo "Validando Autonom.ia Agent Harness v2..."

# Read enforcement_mode
mode="warn"
[ -f "platform-manifest.json" ] && mode=$(jq -r '.enforcement_mode // "warn"' platform-manifest.json 2>/dev/null || echo "warn")
echo "Modo: $mode"
echo

missing_core=0       # blocks only in strict mode
missing_optional=0   # always warn-only

check_file() {
  local f="$1"
  local severity="${2:-OPTIONAL}"   # CORE or OPTIONAL
  if [ ! -f "$f" ]; then
    echo "[${severity}] FALTA: $f"
    if [ "$severity" = "CORE" ]; then
      missing_core=$((missing_core+1))
    else
      missing_optional=$((missing_optional+1))
    fi
  fi
}

check_section() {
  local f="$1"
  local section="$2"
  if [ -f "$f" ] && ! grep -qi "$section" "$f"; then
    echo "[OPTIONAL] seção '$section' ausente em $f"
    missing_optional=$((missing_optional+1))
  fi
}

# Layer 1 — Core (CORE = blocks only in strict mode)
echo "--- Layer 1: Core ---"
check_file "AGENTS.md" CORE
check_file "CLAUDE.md" CORE
check_file "platform-manifest.json" CORE
check_file "REVIEW.md" OPTIONAL
check_section "AGENTS.md" "Model Routing\|Model routing"
check_section "AGENTS.md" "Audit Trail\|Audit trail"
check_section "AGENTS.md" "Environment"

# Layer 2 — Capabilities (all optional — warn only)
echo "--- Layer 2: Capabilities ---"
for agent in implementer reviewer planner tester ops-agent; do
  check_file ".claude/agents/${agent}.md" OPTIONAL
done
for skill in systematic-debugging pr-review rollback-planning memory-compaction issue-shaping; do
  check_file "skills/${skill}/SKILL.md" OPTIONAL
  check_section "skills/${skill}/SKILL.md" "Trigger\|Quando usar"
  check_section "skills/${skill}/SKILL.md" "Workflow\|Passo"
done

# Layer 3 — Infra (all optional — repos sem Claude Code não precisam)
echo "--- Layer 3: Infra ---"
check_file ".claude/hooks/pre-tool-use.sh" OPTIONAL
check_file ".claude/hooks/post-tool-use.sh" OPTIONAL
check_file ".claude/settings.json" OPTIONAL
check_file ".github/pull_request_template.md" OPTIONAL
check_file "scripts/validate-harness.sh" OPTIONAL
check_file "scripts/harness-doctor.sh" OPTIONAL
if [ -f ".claude/hooks/pre-tool-use.sh" ]; then
  if ! head -1 ".claude/hooks/pre-tool-use.sh" | grep -q "#!/"; then
    echo "[OPTIONAL] pre-tool-use.sh não é script shell válido"
    missing_optional=$((missing_optional+1))
  fi

  safe_payload='{"tool_name":"Bash","tool_input":{"command":"rm -rf /tmp/appsell-hook-test"}}'
  root_payload='{"tool_name":"Bash","tool_input":{"command":"rm -rf /"}}'
  if ! printf '%s' "$safe_payload" | bash .claude/hooks/pre-tool-use.sh >/dev/null 2>&1; then
    echo "[OPTIONAL] pre-tool-use.sh bloqueia remoção segura em /tmp"
    missing_optional=$((missing_optional+1))
  fi
  if printf '%s' "$root_payload" | bash .claude/hooks/pre-tool-use.sh >/dev/null 2>&1; then
    echo "[OPTIONAL] pre-tool-use.sh não bloqueia remoção da raiz"
    missing_optional=$((missing_optional+1))
  fi
fi

# Layer 4 — Observatory (all optional)
echo "--- Layer 4: Observatory ---"
for mem in MEMORY_CORE MEMORY_SESSION MEMORY_PROJECT MEMORY_USER MEMORY_LEARNINGS MEMORY_DECISIONS; do
  check_file "docs/memory/${mem}.md" OPTIONAL
done
check_file "docs/STATUS.md" OPTIONAL

# platform-manifest version
if [ -f "platform-manifest.json" ]; then
  version=$(jq -r '.version // "missing"' platform-manifest.json 2>/dev/null || echo "parse-error")
  if [ "$version" = "missing" ] || [ "$version" = "parse-error" ]; then
    echo "[OPTIONAL] platform-manifest.json sem version field"
    missing_optional=$((missing_optional+1))
  else
    echo "Harness version: $version (mode: $mode)"
  fi
fi

echo
echo "Resumo: CORE faltando=$missing_core | OPTIONAL faltando=$missing_optional"

# Strict mode: exit 1 if any CORE missing. Warn mode: always exit 0.
if [ "$mode" = "strict" ] && [ $missing_core -gt 0 ]; then
  echo "FAIL (strict mode): $missing_core arquivos CORE ausentes."
  exit 1
fi

if [ $missing_core -eq 0 ] && [ $missing_optional -eq 0 ]; then
  echo "Harness baseline v2 OK — todos os arquivos presentes."
else
  echo "Harness v2 incompleto — warnings acima. Não bloqueia (modo warn)."
fi
exit 0
