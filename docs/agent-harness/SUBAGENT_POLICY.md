# Subagent Policy

Subagentes servem para subtarefas independentes e limitadas. O agente principal conserva responsabilidade pelo diff, verificação e decisão final.

## Owner de orquestração

Cada tarefa declara um único owner: Harness ou Superpowers. Usar uma skill do outro conjunto não autoriza duplicar planner, implementer, reviewer, tester ou revisão final. Se `orchestration.owner=harness`, o Harness controla os gates; se for `superpowers`, o Harness conserva somente as políticas de segurança e aprovação.

## Classificação antes da delegação

Registrar no audit trail: objetivo, `risk_profile`, criticidade/adversarialidade, classe de modelo escolhida, arquivos permitidos e critérios de aceite.

| Classe | Regra mínima |
|---|---|
| `lean` | Pode usar classe fast/cheap para busca, inventário e resumo sem mutação. |
| `standard` | Implementação/review usa pelo menos a classe definida no plano; resultado deve ser verificado pelo agente principal. |
| `critical` | Não permite downgrade automático de modelo. Exige modelo strong/capable, revisão independente e aprovação humana nos gates vermelhos. |
| `adversarial` | Não permite downgrade automático de modelo, mesmo por custo, indisponibilidade ou timeout. Falha fechada e retorna bloqueio. |

## Regras executáveis

1. Antes de delegar, declarar a classe e o risco no audit trail.
2. Limitar paths e proibir merge, deploy, produção, secrets, billing e dados de cliente sem autorização específica.
3. Não aceitar a mensagem de sucesso do subagente como prova; revisar diff e executar os checks aplicáveis.
4. Em tarefa `critical` ou `adversarial`, se a classe requerida estiver indisponível, parar com `BLOCKED_MODEL_TIER`; nunca rotear silenciosamente para tier inferior.
5. Registrar resultado, verificação independente e decisão do agente principal no audit trail.
6. Todos os reviewers previstos concluem antes de qualquer correção.
7. Consolidar findings em uma onda por rodada e limitar o re-review ao delta.
8. Fazer uma única revisão ampla final.

O budget pode reduzir paralelismo ou adiar a tarefa. Ele não pode reduzir automaticamente a classe de modelo exigida por risco.
