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

## Sessão: 2026-07-13 11:05 — inspeção superadmin temporária da demo
Issue: #56
Branch: feat/56-superadmin-demo-inspection
PR: não aberta
Estado: em_progresso
Modelo: strong + human
Arquivos alterados: migration 0007, rotas FastAPI/BFF de inspeção, ticket HttpOnly, grant/capabilities, shell superadmin, reset demo, testes PG/API/BFF/browser e docs
Comandos executados: test-postgres-foundation; lint; typecheck; Vitest; Ruff; mypy; pytest; pip-audit; pnpm audit; build; Playwright Chromium/WebKit; production fail-closed; inspeção no navegador interno 390 px
Validação: local verde após dois ciclos RED corrigidos — 126 Python/PG/API, 83 web, 4 verticais BFF/API/PG, 30 browser + 4 skips deliberados, 1 production fail-closed; build e audits verdes; callback sem Domain API falha fechado; OpenAPI desabilitado; token/sessão ausentes do HTML/RSC bruto
Bloqueios: review crítico independente, commit/push, Draft PR e CI remoto; Identity real #13, staging, KMS #19, Financial/Neuro e produção continuam bloqueados
Próxima ação: review crítico do hash final após correções; somente se P0-P3=0, commitar/push e abrir Draft PR empilhada sobre #36; não fazer merge/deploy
Project status:
  - Projeto: Studio
  - Status: Em desenvolvimento
  - Tipo: Feature
  - Prioridade: P0
  - Risco: Alto
  - Ambiente: Local
  - Próxima ação: gates + novo review crítico P0-P3=0; depois commit/push, Draft PR e CI exato
