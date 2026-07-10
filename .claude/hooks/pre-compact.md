# Hook: PreCompact

Antes de compactar contexto, o agente DEVE:

1. Executar skill `memory-compaction`.
2. Atualizar `docs/memory/MEMORY_SESSION.md` com estado completo:
   - issue, branch, PR
   - estado atual (em_progresso | bloqueado | concluído)
   - arquivos alterados
   - comandos executados
   - validação
   - bloqueios
   - próxima ação exata
   - project status
3. Atualizar `docs/PROGRESS.md` e `docs/STATUS.md`.
4. Nunca compactar com bloqueio aberto não documentado em MEMORY_SESSION.md.
