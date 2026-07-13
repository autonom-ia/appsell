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

## Sessão: 2026-07-13 09:58 — hardening da tenant demo e curso Codex
Issue: #33
Branch: feat/33-demo-tenant
PR: #36
Estado: em_progresso
Modelo: strong + human
Arquivos alterados: reset demo/hostaddr/env libpq, memberships/grants, allowlist server-side buyer/admin, cookies CSRF, layout de produtos, PDFs/manifests, testes PG/BFF/browser e docs
Comandos executados: test-postgres-foundation; lint; typecheck; Vitest; Ruff; mypy; pytest; pip-audit; pnpm audit; build; Playwright Chromium/WebKit; production fail-closed; inspeção PDF por stream e raster
Validação: local verde — 113 Python/PG/API, 74 web, 2 verticais BFF/API/PG, 30 browser + 4 skips deliberados, 1 production fail-closed; build e audits verdes
Bloqueios: review crítico do delta staged e CI remoto; superadmin auditado da Issue #33; Identity real #13, KMS #19, Financial/Neuro e produção continuam bloqueados
Próxima ação: concluir review crítico; se P0-P2=0, commitar/push na PR #36 Draft e aguardar CI exato; não fazer merge/deploy
Project status:
  - Projeto: Studio
  - Status: Ajustes
  - Tipo: Feature
  - Prioridade: P1
  - Risco: Alto
  - Ambiente: Local
  - Próxima ação: review crítico do staged; depois commit/push e CI exato
