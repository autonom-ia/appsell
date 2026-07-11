# MEMORY_SESSION.md — Estado da tarefa atual

Atualizado continuamente pelo agente durante a sessão. Compactado/persistido em handoff e Stop hook.

## Schema obrigatório

```markdown
## Sessão: [YYYY-MM-DD HH:MM] — [objetivo em uma linha]
Issue: #N
Branch: [nome]
PR: #N (ou "não aberta")
Estado: em_progresso | bloqueado | concluído
  → em_progresso: agente está trabalhando, pode retomar imediatamente
  → bloqueado: aguarda aprovação ou decisão — campo Bloqueios deve estar preenchido
  → concluído: tarefa encerrada — PR aberta ou trabalho entregue
  (transição para "concluído" só após PR aberta ou resultado validado)
Modelo: fast | medium | strong | humano
Arquivos alterados: [lista]
Comandos executados: [lista crítica]
Validação: [resultado ou "não rodou — motivo"]
Bloqueios: [lista com dono e ação esperada | "nenhum"]
Próxima ação: [exata — o que fazer ao retomar]
Project status:
  - Projeto: [nome]
  - Status: [Backlog | Investigando | Em desenvolvimento | PR aberta | Em review | Ajustes | Aprovada | Mergeada | Bloqueada]
  - Tipo: [Bug | Feature | Infra | Docs | Refactor | Segurança]
  - Prioridade: [P0 | P1 | P2 | P3]
  - Risco: [Baixo | Médio | Alto]
  - Ambiente: [Local | Dev | Staging | Produção]
  - Próxima ação: [acionável]
```

## Sessão atual

<!-- Substituir abaixo a cada nova sessão. Sessões anteriores vão para PROGRESS.md. -->

## Sessão: 2026-07-11 07:50 — foundation PostgreSQL, tenancy e bootstrap one-shot
Issue: #24
Branch: feat/24-domain-foundation
PR: não aberta
Estado: em_progresso
Modelo: strong + human
Arquivos alterados: apps/api, migration Alembic, compose/Postgres init, CI, env/README, scripts e docs de status/audit
Comandos executados: Harness doctor; uv lock/sync/ruff/mypy/pytest/pip-audit; Docker compose migration/PG/API; pnpm lint/typecheck/test/audit/build/E2E; reviews críticos
Validação: local verde — 7 API/config, 25 PostgreSQL/RLS/bootstrap, 55 web, 17 browser, 1 production; container non-root healthy; cleanup sem resíduos
Bloqueios: CI remoto; Identity real #13, KMS #19 e produção continuam bloqueados
Próxima ação: abrir PR parcial da #24, atualizar Project e executar CI remoto
Project status:
  - Projeto: AppSell
  - Status: Em desenvolvimento
  - Tipo: Feature
  - Prioridade: P1
  - Risco: Médio
  - Ambiente: Dev
  - Próxima ação: review final, PR parcial e CI remoto
