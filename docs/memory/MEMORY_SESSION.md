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

## Sessão: 2026-07-15 16:40 — publicar runtime preview isolado no EasyPanel
Issue: #77 (parent #66)
Branch: infra/77-easypanel-runtime-preview
PR: não aberta
Estado: em_progresso
Modelo: strong + human
Arquivos alterados: Compose/env/bootstrap/E2E EasyPanel; loader/preflight/up/verify/teardown com rollback; verifier de artifact em modo Appsell-only; workflow; Playwright runtime; docs
Comandos executados: Bash syntax; fixtures do env/verifier; Compose real na VPS sem criação de recurso; ESLint; TypeScript; Vitest; Next build; Harness Doctor 2.1.2; inspeção de CI e artifacts da PR #76
Validação: PR #76 12/12 checks e review crítico GREEN; slice #77 com Compose/artefatos/ESLint/typecheck/build e 295 Vitest verdes; Harness 0 erros/0 warnings; reviews crítico e isolamento GREEN com P0/P1/P2=0; lifecycle mockado passou no Docker real da VPS sem criar runtime
Bloqueios: obter autorização explícita para commit/push/Draft PR e fechar CI antes de qualquer deploy; Identity/GitHub OAuth, Financial, Neuro.ai e mídia de produção são slices posteriores
Próxima ação: após autorização, commitar, fazer push e abrir Draft PR #77 sem merge/deploy; aguardar CI completo
Project status:
  - Projeto: Autonom.ia Dev
  - Status: Em desenvolvimento
  - Tipo: Infra
  - Prioridade: P0
  - Risco: Alto
  - Ambiente: Staging
  - Próxima ação: autorização para commit/push/Draft PR; depois CI, sem merge/deploy
