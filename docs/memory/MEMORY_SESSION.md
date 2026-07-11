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

## Sessão: 2026-07-11 10:27 — produto e curso draft persistentes
Issue: #28
Branch: feat/28-product-course-drafts
PR: não aberta
Estado: em_progresso
Modelo: strong + human
Arquivos alterados: migration 0003, FastAPI catálogo, BFF seller/product, workspace mobile, CSRF, testes PG/API/BFF/E2E, compose e docs
Comandos executados: lint; typecheck; Vitest; Ruff; mypy; pytest; build; Playwright; test-postgres-foundation; reviews críticos
Validação: local verde — 67 web, 68 PostgreSQL/API, 2-BFF real, 22 browser + 2 skips; preview local persiste tenant/produto/curso; três reviews P0-P2=0
Bloqueios: CI remoto; Identity real #13, KMS #19 e produção continuam bloqueados
Próxima ação: abrir PR da #28, atualizar Project, executar CI remoto e não fazer deploy
Project status:
  - Projeto: sem opção AppSell no campo do Project
  - Status: Em desenvolvimento
  - Tipo: Feature
  - Prioridade: P0
  - Risco: Alto
  - Ambiente: Dev
  - Próxima ação: PR, CI remoto e review
