# STATUS — Health snapshot do projeto

Atualizado em pontos de marco (PR aberta, deploy, incidente). Diferente de MEMORY_SESSION.md (que é estado da tarefa atual) — STATUS é estado do PROJETO.

## Indicadores

| Indicador | Status | Última verificação |
|-----------|:------:|--------------------|
| Build | 🟢 verde / 🟡 warning / 🔴 vermelho | [YYYY-MM-DD] |
| Testes | 🟢 / 🟡 / 🔴 | [YYYY-MM-DD] |
| CI | 🟢 / 🟡 / 🔴 | [YYYY-MM-DD] |
| Deploy produção | 🟢 / 🟡 / 🔴 | [YYYY-MM-DD] |
| Harness instalado | [versão] | [YYYY-MM-DD] |
| Harness drift | OK / DEGRADED | [YYYY-MM-DD] |

## Estado atual

[1-2 frases descrevendo onde o projeto está hoje. Não inventar — verificar antes.]

## Última mudança relevante

[PR ou commit que mudou estado significativo. Data + link.]

## O que está funcionando

- [Componente 1] — [evidência: teste, healthz, métrica]
- [Componente 2] — [evidência]

## O que está quebrado / em degradação

- [Componente X] — [sintoma] — [Issue #N]
- [Componente Y] — [sintoma] — [Issue #M ou "ainda não issued"]

## Bloqueios

- [Bloqueio] — [dono] — [ação esperada]

## Próxima ação

[Acionável para o próximo agente/humano que retomar este projeto.]

## Links

- Project: https://github.com/users/autonom-ia/projects/3
- Issues abertas: [link gh issue list]
- PRs abertas: [link gh pr list]
- Runbook: [docs/runbook.md ou "não existe"]
- Health report mais recente: [docs/harness-health/doctor-YYYY-MM-DD.md ou "nunca rodou"]
