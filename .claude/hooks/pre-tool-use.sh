#!/usr/bin/env bash
# pre-tool-use.sh — observes tool calls; blocks only catastrophic patterns in strict mode
# Called by Claude Code runtime before every tool call.
# stdin: JSON { "tool_name": "...", "tool_input": { ... } }
# Behavior depends on enforcement_mode in platform-manifest.json:
#   "warn" (default): stderr message but exit 0 — Claude proceeds
#   "strict": exit 1 on BLOCKED patterns — Claude is blocked
# CATASTROPHIC patterns (always exit 1, regardless of mode):
#   - "rm -rf /"  (root fs delete)
#   - "DROP DATABASE"  (only blocked literally; ALTER/DROP TABLE are warn)
#   - "destroyService"  (Easypanel destroy, irreversible)
# Everything else is observability: log to stderr, exit 0.

set -uo pipefail

input=$(cat)

# Empty stdin = no tool call data; exit 0 (warn mode default — don't block on missing data)
if [ -z "$input" ]; then
  echo "INFO: pre-tool-use received empty stdin" >&2
  exit 0
fi

tool_input=$(echo "$input" | jq -r 'tostring' 2>/dev/null || echo "$input")

# Read enforcement_mode (default: warn)
mode="warn"
if [ -f "platform-manifest.json" ]; then
  mode=$(jq -r '.enforcement_mode // "warn"' platform-manifest.json 2>/dev/null || echo "warn")
fi

# CATASTROPHIC — always block, even in warn mode
CATASTROPHIC=(
  "DROP DATABASE"
  "destroyService"
)

# STRICT-only block patterns (warn in warn mode)
STRICT_BLOCK=(
  "DROP TABLE"
  "TRUNCATE"
  "deleteService"
  "push --force" "push -f"
  "reset --hard"
  "clean -fd"
  " wipe " " purge "
)

# Always warn (never block)
WARN_PATTERNS=(
  "production" "prod/"
  "\.env"
  "secret" "credential" "token" "password" "api.key" "apikey"
  "billing" "ALTER TABLE" "migrate"
)

# Root filesystem deletion needs a token boundary. A substring check would also
# reject safe paths such as /tmp/appsell-build.
if echo "$tool_input" | grep -Eqi 'rm[[:space:]]+-rf[[:space:]]+/([*]|\\[*])?([[:space:];&|",}]|$)'; then
  echo "BLOCKED (catastrophic): input attempts to remove the root filesystem. Requires explicit Rodrigo approval." >&2
  exit 1
fi

# Other catastrophic checks — always block
for pattern in "${CATASTROPHIC[@]}"; do
  if echo "$tool_input" | grep -qiF "$pattern"; then
    echo "BLOCKED (catastrophic): input contains '$pattern'. Requires explicit Rodrigo approval." >&2
    exit 1
  fi
done

# Strict-only block check
if [ "$mode" = "strict" ]; then
  for pattern in "${STRICT_BLOCK[@]}"; do
    if echo "$tool_input" | grep -qiF "$pattern"; then
      echo "BLOCKED (strict mode): input matches '$pattern'. Set enforcement_mode='warn' to allow." >&2
      exit 1
    fi
  done
else
  # In warn mode, strict patterns just become warnings
  for pattern in "${STRICT_BLOCK[@]}"; do
    if echo "$tool_input" | grep -qiF "$pattern"; then
      echo "WARN (warn mode): input matches '$pattern' — would block in strict mode." >&2
    fi
  done
fi

# Always-warn patterns (informational only)
for pattern in "${WARN_PATTERNS[@]}"; do
  if echo "$tool_input" | grep -qiF "$pattern"; then
    echo "INFO: input touches '$pattern' — verify this is safe." >&2
  fi
done

exit 0
