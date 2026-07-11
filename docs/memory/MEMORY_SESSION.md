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

## Sessão: 2026-07-11 22:00 — player, progresso e retomada multitenant
Issue: #38
Branch: feat/38-learning-progress
PR: #39
Estado: pr_aberta
Modelo: strong + human
Arquivos alterados: migration 0007, FastAPI/BFF learning, player e library mobile-first, asset stream tenant-bound, testes PG/API/BFF/E2E e docs
Comandos executados: lint; typecheck; Vitest; Ruff; mypy; pytest; audit; build; Playwright Chromium/WebKit; production fail-closed; test-postgres-foundation; review de design/tenancy/API
Validação: local verde — 84 web, 137 PostgreSQL/API, 3 BFF verticais reais, 42 browser + 4 skips deliberados; build sem warning e audits verdes
Bloqueios: CI/review remoto; Identity real #13, KMS #19, Financial/Neuro, storage externo e produção continuam bloqueados
Próxima ação: aguardar CI e review do SHA da PR #39; não fazer merge/deploy
Project status:
  - Projeto: Studio
  - Status: PR aberta
  - Tipo: Feature
  - Prioridade: P0
  - Risco: Alto
  - Ambiente: Local
  - Próxima ação: aguardar CI e review do SHA da PR #39
