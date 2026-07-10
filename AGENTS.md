<!-- AUTONOMIA_AGENT_HARNESS_START -->
## Autonom.ia Agent Harness

Este projeto usa o Autonom.ia Agent Harness.

Fonte local:
`~/Documents/agent-harness`

Fonte oficial:
`https://github.com/autonom-ia/agent-harness`

Fluxo obrigatório:
`Issue → Branch → PR → Project update → Review → Approval → Merge → Deploy/Rollback plan`

Project:
`https://github.com/users/autonom-ia/projects/3`

Regras:
- Preservar regras específicas deste projeto.
- Em caso de conflito entre regra local e harness, pedir decisão ao Rodrigo.
- Não fazer merge sem aprovação explícita do Rodrigo.
- Não sobrescrever AGENTS.md, CLAUDE.md, CURSOR.md, OPENCODE.md, templates ou docs locais sem comparar.
<!-- AUTONOMIA_AGENT_HARNESS_END -->

# AGENTS.md

## Contexto

Você está trabalhando em um repositório da Autonom.ia, do Rodrigo, incluindo projetos Hub2You.

## Comportamento

- Responder em português brasileiro.
- Ser direto, prático, cético e baseado em evidência.
- Tratar Rodrigo como operador técnico/product owner.
- Separar fatos, hipóteses e recomendações.
- Não inventar arquitetura, credenciais, logs, regras de negócio ou requisitos.
- Inspecionar repo/docs/configs/logs antes de afirmar.

## Fluxo obrigatório

```text
Issue → Branch → PR → Project update → Review → Approval → Merge → Deploy/Rollback plan
```

## Harness

Se Rodrigo pedir para verificar, instalar ou atualizar o harness:

- acessar a fonte oficial;
- ler `BOOTSTRAP.md`;
- verificar `.autonomia-harness.json`;
- instalar ou atualizar por PR;
- preservar regras locais;
- reportar conflitos;
- não fazer merge.

Fonte oficial:

```text
https://github.com/autonom-ia/agent-harness
```

## Git

- Checar `git status` antes de editar.
- Não trabalhar direto na `main` sem instrução explícita.
- Não sobrescrever mudanças do usuário.
- Não fazer commit, push, merge, rebase, reset hard ou force push sem aprovação explícita.
- PRs devem ser pequenas, escopadas, vinculadas à Issue e revisáveis.
- Para épicos parciais, usar `Refs #...` ou `Part of #...`.
- Usar `Closes #...` apenas quando a PR encerrar a Issue inteira.

## GitHub Project

Sempre usar:

Project: Autonom.ia Dev
URL: https://github.com/users/autonom-ia/projects/3

Campos obrigatórios:

- Projeto
- Status
- Tipo
- Prioridade
- Risco
- Próxima ação
- Ambiente

Se não houver acesso ao Project, adicionar na Issue/PR:

```md
## Project update pendente

- Projeto:
- Status:
- Tipo:
- Prioridade:
- Risco:
- Próxima ação:
- Ambiente:
```

## Memória

Usar e atualizar quando aplicável:

- docs/memory/MEMORY_PROJECT.md
- docs/memory/MEMORY_USER.md
- docs/memory/MEMORY_SESSION.md
- docs/memory/MEMORY_LEARNINGS.md
- docs/memory/MEMORY_DECISIONS.md
- docs/memory/MEMORY_COMPACTION.md

Antes de compactar ou encerrar tarefa longa, atualizar MEMORY_SESSION.md.

## Segurança

- Nunca imprimir ou commitar secrets, tokens, cookies, private keys, database URLs ou credenciais.
- Mascarar valores sensíveis.
- Tratar produção, dados de cliente, auth, billing, WhatsApp, WAHA, Chatwoot, Docker Swarm e bancos como alto risco.

## Execução

- Inspecionar antes de corrigir.
- Fazer mudanças pequenas e reversíveis.
- Rodar testes/lint/typecheck/build quando aplicável.
- Se não puder validar, explicar claramente.
- Revisar diff para regressões, secrets, hardcoded values e mudanças fora de escopo.

## Documentação

Atualizar README, docs, `.env.example`, runbooks ou troubleshooting quando comportamento, setup, deploy, env vars, integrações ou operação mudarem.

## Final da resposta técnica

Sempre encerrar com:

- objetivo;
- o que foi inspecionado;
- o que mudou;
- arquivos alterados;
- comandos rodados;
- validação;
- Project status;
- riscos;
- próximo passo.

## Model Routing

Regra obrigatória — declarar classe de modelo no audit trail:

| Tarefa | Classe |
|--------|--------|
| resumir / listar / buscar / grep | fast / cheap |
| bug simples / PR pequena / refactor | medium |
| arquitetura / auth / billing / produção | strong + human |
| deploy / migration / secret rotation | human executa; agente planeja |

## Environment-Aware Behavior

- `dev / local`: autonomia para ler, editar, rodar testes, abrir PRs.
- `staging`: autonomia para ler e testar; deploy requer aprovação.
- `production`: toda escrita (env, DB, config, deploy) requer aprovação explícita antes de executar.
- Identificar ambiente por: variável `APP_ENV`, URL, nome do repo, `.autonomia-env` se existir.

## Audit Trail

Ao encerrar sessão ou receber instrução de handoff, escrever em `docs/audit/session-YYYY-MM-DD-HHMMSS.md`:
- issue, branch, PR
- tools chamadas com resultado resumido
- comandos executados
- decision points (situação → decisão → motivo)
- validação final
- próxima ação exata

## Self-Assessment

Quando detectar harness instalado (existe `platform-manifest.json`), verificar:
1. Versão instalada vs. versão em `~/Documents/agent-harness/VERSION` (local) ou source conhecido.
2. Se desatualizado: reportar e sugerir `harness-install-or-update`.
3. Não bloquear trabalho por harness desatualizado — apenas reportar.
