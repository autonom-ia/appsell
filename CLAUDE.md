<!-- AUTONOMIA_AGENT_HARNESS_START -->
@AGENTS.md

## Autonom.ia Agent Harness

Este projeto usa o Autonom.ia Agent Harness.

Fonte oficial:
`https://github.com/autonom-ia/agent-harness`

Fonte local preferencial:
`~/Documents/agent-harness`

Project:
`https://github.com/users/autonom-ia/projects/3`

Regras:
- Preservar regras específicas deste projeto.
- Em caso de conflito entre regra local e harness, pedir decisão ao Rodrigo.
- Operar pelas faixas de `.claude/rules/prod-approval.md`, com a regra local mais restritiva prevalecendo: merge, deploy, rebase, reset hard, rotação de secrets, migration de produção e alteração de infraestrutura exigem aprovação explícita do Rodrigo.
- Não fazer merge sem aprovação explícita do Rodrigo.
<!-- AUTONOMIA_AGENT_HARNESS_END -->


# CLAUDE.md

Estas instruções complementam o AGENTS.md para Claude Code.

## Claude Code

- Antes de mudanças não triviais, usar planejamento.
- Para tarefas de alto risco, propor plano e aguardar aprovação explícita.
- Não usar a mesma sessão para implementar e aprovar o próprio trabalho.
- Usar `.claude/rules/` para regras por caminho.
- Usar `.claude/agents/` para subagentes especializados.
- Usar `.claude/skills/` para skills sob demanda.
- Usar hooks para sensores, bloqueios e memória quando configurados.

## Limites

Não fazer merge, deploy, rebase, reset hard, rotação de secrets, migration de produção ou alteração de infraestrutura sem aprovação explícita do Rodrigo.
