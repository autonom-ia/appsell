# Hook: Stop

Ao encerrar sessão (Stop event), o agente DEVE:

1. Renomear `docs/audit/session-current.jsonl` para
   `docs/audit/session-YYYY-MM-DD-HHMMSS.jsonl`.
2. Criar `docs/audit/session-YYYY-MM-DD-HHMMSS.md` com:
   - agente, modelo (classe), issue, branch, PR
   - tools chamadas (do jsonl)
   - decision points (situação → decisão → motivo)
   - validação final (passed | failed | partial)
   - próxima ação exata
   - project status
3. Atualizar `docs/memory/MEMORY_SESSION.md`.
4. Se PR não foi aberta e trabalho foi feito: criar nota de handoff.
