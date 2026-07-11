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

## Sessão: 2026-07-11 13:38 — currículo multitenant e snapshot v2
Issue: #32
Branch: feat/32-curriculum
PR: #35
Estado: pr_aberta
Modelo: strong + human
Arquivos alterados: migration 0005, FastAPI/BFF curriculum CRUD/order/archive, internal envelope, snapshot v2, editor currículo, CSRF por ação, testes PG/API/BFF/E2E e docs
Comandos executados: lint; typecheck; Vitest; Ruff; mypy; pytest; audit; build; Playwright Chromium/WebKit; production fail-closed; test-postgres-foundation; review de design/tenancy/API
Validação: local verde — 69 web, 94 PostgreSQL/API, BFF vertical real em duas instâncias, 26 browser + 4 skips deliberados; build e audit verdes
Bloqueios: PR/reviews/CI remoto; Identity real #13, KMS #19, Financial/Neuro e produção continuam bloqueados
Próxima ação: aguardar App CI e review do SHA remoto da PR #35; depois executar Issue #33 da tenant demo/curso Codex e Issue #34 do Harness 2.1.2; não fazer deploy
Project status:
  - Projeto: Studio
  - Status: Em desenvolvimento
  - Tipo: Feature
  - Prioridade: P0
  - Risco: Alto
  - Ambiente: Local
  - Próxima ação: revisar delta, abrir PR e executar CI exato
