# Session Audit Log — Template

> Copiar este arquivo para `docs/audit/session-YYYY-MM-DD-HHMMSS.md` ao encerrar sessão.
> Hook `stop.md` gera automaticamente; preenchimento manual em plataformas sem hook (Codex/Cursor).

## Metadata

Data: [YYYY-MM-DD HH:MM UTC]
Agente: Claude Code | Codex | Cursor
Modelo: fast | medium | strong | humano
Issue: #N (ou "sem Issue — investigação")
Branch: [nome]
PR: #N (ou "não aberta")

## Tools chamadas

<!-- Se hook PostToolUse está ativo (.claude/hooks/post-tool-use.sh): gerado de docs/audit/session-current.jsonl. Se hook ausente (Codex/Cursor ou Claude Code sem settings.json): preencher manualmente os tool calls mais relevantes. -->

| Timestamp | Tool | Exit code |
|-----------|------|-----------|
| [ISO 8601] | [Bash | Edit | Read | Write | Agent | ...] | [0 | N] |

## Comandos executados

| Comando | Resultado | Notas |
|---------|-----------|-------|
| `[comando]` | [ok | erro | partial] | [contexto] |

## Skills chamadas

| Skill | Trigger | Resultado |
|-------|---------|-----------|
| [systematic-debugging | pr-review | ...] | [o que disparou] | [output esperado entregue?] |

## Subagentes despachados

| Agent | Task | Status |
|-------|------|--------|
| [implementer | reviewer | ...] | [task atribuída] | [DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED] |

## Decision points

| Situação | Decisão | Motivo |
|----------|---------|--------|
| [ex: spec ambíguo em X] | [ex: perguntar Rodrigo opção A vs B] | [ex: ambiguidade poderia mudar escopo] |

## Erros encontrados

| Erro | Root cause (hipótese ou confirmada) | Resolução |
|------|-------------------------------------|-----------|
| [mensagem literal] | [causa] | [como foi resolvido | "aberto"] |

## Validação final

Status: passed | failed | partial
Detalhe: [o que passou / o que falhou / o que ficou pendente]

## Próxima ação

[Ação exata — suficiente para retomar sem contexto adicional. Não "continuar" — deve ser acionável.]

## Project status

| Campo | Valor |
|-------|-------|
| Projeto | |
| Status | |
| Tipo | |
| Prioridade | |
| Risco | |
| Ambiente | |
| Próxima ação | |

## Aprendizados gerados nesta sessão

<!-- Se houve aprendizado generalizável, criar entrada em MEMORY_LEARNINGS.md e linkar aqui -->

- [Aprendizado X] → MEMORY_LEARNINGS.md#aprendizado-N

## Incidentes registrados

<!-- Se houve incidente, criar entrada em MEMORY_INCIDENTS.md e linkar aqui -->

- [Incidente Y] → MEMORY_INCIDENTS.md#incidente-N
