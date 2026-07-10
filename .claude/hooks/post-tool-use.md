# Hook: PostToolUse

Executa `post-tool-use.sh` após cada tool call.

Appenda linha JSON em `docs/audit/session-current.jsonl`:
{ "ts": "ISO8601", "tool": "tool_name", "exit_code": N }

Ao encerrar sessão, renomear session-current.jsonl para
session-YYYY-MM-DD-HHMMSS.jsonl via hook Stop ou manualmente.
