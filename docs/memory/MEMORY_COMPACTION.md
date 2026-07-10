# MEMORY_COMPACTION.md — Política de compactação

Regras para quando contexto chegar a ~20% restante ou em handoff entre sessões/agentes.

## Nunca perder (preservar em MEMORY_SESSION antes de compactar)

- Objetivo da tarefa (1 linha)
- Issue: #N
- Branch atual
- PR: #N (ou "não aberta")
- Estado (em_progresso | bloqueado | concluído)
- Modelo usado (fast | medium | strong | humano)
- Decisões do Rodrigo registradas na sessão
- Arquivos alterados (lista)
- Comandos executados críticos
- Resultados de validação
- Bloqueios abertos com dono
- Rollback plan se houver mudança em produção pendente
- Project status (7 campos)
- Próxima ação exata (suficiente para retomar sem contexto adicional)

## Pode descartar

- Logs repetidos
- Outputs longos já resumidos
- Conversa social
- Hipóteses descartadas (manter motivo, não detalhe)
- Tentativas abandonadas (manter aprendizado se houver)
- Erros transitórios já resolvidos sem aprendizado

## Procedimento

### Antes de compactar
1. Atualizar `MEMORY_SESSION.md` com schema completo
2. Atualizar `docs/PROGRESS.md` com linha temporal
3. Atualizar `docs/STATUS.md` com health snapshot
4. Verificar: sem `TODO`/`PENDENTE`/`BLOQUEIO` sem dono em MEMORY_SESSION
5. Se houve aprendizado generalizável: adicionar em MEMORY_LEARNINGS antes de compactar

### Durante compactação
Executar comando de compactação do runtime (Claude Code: `/compact`, ou aceitar automática).

### Depois de compactar
1. Ler MEMORY_SESSION imediatamente
2. Validar continuidade: issue, branch, PR, próxima ação ainda fazem sentido
3. Se algo essencial sumiu: recuperar de PROGRESS.md ou Git log
4. Não prosseguir trabalho sem confirmação de estado

## Auto-assessment pós-compactação (regra Rodrigo #5)

Após compactação, ler imediatamente os artefatos críticos:
- MEMORY_SESSION (esta sessão)
- doc mestre da fase atual (ex: `docs/REFACTOR_X_FASES_*.md` se aplicável)
- audits relevantes em `docs/audit/`
- Project status atual

Sem essa releitura, agente trabalha cego pós-compactação.
