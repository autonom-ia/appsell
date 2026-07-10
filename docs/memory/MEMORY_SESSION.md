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

## Sessão: 2026-07-10 15:34 — Redis OAuth store isolado e fail-closed
Issue: #14
Branch: feat/14-redis-oauth-store
PR: não aberta
Estado: em_progresso
Modelo: strong + human
Arquivos alterados: lib/redis-oauth-service.ts; lib/oauth-service.server.ts; lib/crypto.ts; rotas OAuth/session; next.config.ts; tests/redis-oauth-store.test.ts; tests/bff-multiprocess.test.ts; tests/helpers/isolated-redis.ts; scripts/with-isolated-redis.mjs; package.json; pnpm-lock.yaml; .env.example; README.md; docs/PROGRESS.md
Comandos executados: pnpm add ioredis; pnpm lint; pnpm typecheck; pnpm test; pnpm build; pnpm test:e2e; pnpm test:e2e:production; pnpm audit --prod; git diff --check
Validação: lint/typecheck/build verdes; Vitest 23/23; Playwright Chromium+WebKit 6/6; produção fail-closed 1/1; audit sem vulnerabilidades; Redis 8.8.0 efêmero autenticado em porta aleatória diferente de 6379; dois processos Next reais compartilharam sessão
Bloqueios: nenhum técnico; commit/push/PR e Project update pendentes
Próxima ação: commit, push, abrir PR draft da Issue #14 e atualizar Project sem merge
Project status:
  - Projeto: Infra
  - Status: Em desenvolvimento
  - Tipo: Segurança
  - Prioridade: P0
  - Risco: Alto
  - Ambiente: Dev
  - Próxima ação: abrir PR draft da Issue #14, aguardar App CI e review humano
