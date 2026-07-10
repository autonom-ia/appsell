#!/usr/bin/env bash
# post-tool-use.sh — logs tool calls to audit trail
# stdin: JSON { "tool_name": "...", "tool_input": {...}, "tool_output": {...}, "exit_code": N }

set -uo pipefail

input=$(cat)
tool_name=$(echo "$input" | jq -r '.tool_name // "unknown"' 2>/dev/null || echo "unknown")
exit_code=$(echo "$input" | jq -r '.exit_code // 0' 2>/dev/null || echo "0")

# Validate exit_code is numeric, default to 0
if ! [[ "$exit_code" =~ ^-?[0-9]+$ ]]; then
  exit_code=0
fi

mkdir -p docs/audit 2>/dev/null
if [ ! -w "docs/audit" ]; then
  # Cannot write audit log — fail open (warn mode default), exit 0
  exit 0
fi

SESSION_LOG="docs/audit/session-current.jsonl"
timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

jq -nc --arg ts "$timestamp" --arg tool "$tool_name" --argjson code "$exit_code" '{ts: $ts, tool: $tool, exit_code: $code}' >> "$SESSION_LOG" 2>/dev/null || true

if [ "$exit_code" != "0" ]; then
  echo "POST-TOOL WARNING: $tool_name exited with code $exit_code at $timestamp" >&2
fi

exit 0
