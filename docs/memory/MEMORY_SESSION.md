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

## Sessão: 2026-07-10 14:47 — spike OAuth/BFF com sessão opaca
Issue: #8
Branch: feat/8-oauth-bff-spike
PR: #11 (draft)
Estado: concluído
Modelo: strong + human
Arquivos alterados: scaffold Next.js em app/, lib/, tests/; package.json; pnpm-lock.yaml; README.md; docs/PROGRESS.md; docs/audit/session-2026-07-10-143043.md
Comandos executados: pnpm install; pnpm lint; pnpm typecheck; pnpm test; pnpm build; pnpm audit --prod; pnpm test:e2e; pnpm test:e2e:production; git diff --check; scripts/validate-harness.sh; Playwright CLI; curl em next start
Validação: lint/typecheck/build verdes; Vitest 12/12; Playwright automatizado 6/6 Chromium+WebKit mobile; produção 1/1 com next start; audit sem vulnerabilidades; Harness v2.0.2 completo; não exposição do cookie e replay 401 confirmados
Bloqueios: nenhum técnico; merge continua proibido sem aprovação explícita
Próxima ação: aguardar App CI e review humano da PR #11; não fazer merge
Project status:
  - Projeto: Infra
  - Status: PR aberta
  - Tipo: Segurança
  - Prioridade: P0
  - Risco: Alto
  - Ambiente: Dev
  - Próxima ação: aguardar checks e review humano; depois planejar Identity real e Redis isolado
