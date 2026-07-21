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

## Sessão: 2026-07-21 16:00 — modo produção real na UI (#118) + landing real (#122), entregues e deployados
Issue: #118, #122 (ambas fechadas)
Branch: feat/118-real-mode-dashboard, feat/122-landing-real-entry (mergeadas e removidas)
PR: #121 (squash ad19e23b), #123 (squash 55ee32e6)
Estado: concluído
Modelo: strong + human (produção)
Arquivos alterados: app/api/session/route.ts (campo runtime, sem PII); app/components/dashboard-overview.tsx (novo, catálogo real via /api/sellers + drafts paginados, empty-state, onboarding inline, demo isolado por persona); app/components/shell-profile-context.tsx (novo, sessão resolvida 1x no shell); app/components/app-shell.tsx (banner por modo real, footer, provider); app/(protected)/dashboard/page.tsx; lib/dashboard-projection.ts (novo, métricas puras); app/page.tsx (landing variante identity: sem badge DEMO, CTA 'Entrar na sua conta'); tests/dashboard-projection.test.ts (novo); tests/e2e-real/identity-cross-repo.spec.ts (nome do link)
Comandos executados: typecheck/lint/build; vitest (17 verdes); Playwright mobile-shell+oauth-flow 19/19 local; CI 18/18 nas duas PRs (2 flakes de infra re-rodados: capacity threshold, storage-vertical postgres); deploys web via Easypanel deployService forceRebuild
Validação: prod validada pós-deploy — /api/health/live e /ready ok (redis+api true), landing servindo 'Entrar na sua conta' sem badge DEMO; review cavecrew PR #121 0 vermelho (1 amarelo corrigido: timeout loadCatalog; 1 won't-fix justificado); PR #123 <30 linhas sem reviewer pesado
Bloqueios: nenhum
Próxima ação: #96 (Bloco 4 — monetização real: preço na Offer, checkout externo, worker fulfillment webhook→entitlement) — ver prompt de retomada em ~/dev/APPSELL_SSO_STATUS_2026-07-21.md
Project status:
  - Projeto: Appsell
  - Status: Mergeada (#121, #123)
  - Tipo: Feature
  - Prioridade: P1
  - Risco: Baixo
  - Ambiente: Produção
  - Próxima ação: iniciar #96
