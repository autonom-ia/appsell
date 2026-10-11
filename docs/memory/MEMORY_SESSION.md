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

## Sessão: 2026-07-12 11:32 — vitrine pública seller-scoped mobile-first
Issue: #49
Branch: feat/49-public-storefront
PR: não aberta
Estado: em_progresso
Modelo: strong + human
Arquivos alterados: projeção PostgreSQL/RLS, API e BFF públicos, rate limit Redis, rotas/UI/metadata, testes e docs
Comandos executados: suíte PostgreSQL/API; verticais BFF/browser; Vitest; Playwright; Ruff; mypy; lint; typecheck; build; audits; baseline de 100 tenants; Harness
Validação: local verde — 161 PostgreSQL/API, 3 verticais BFF/browser, 138 Vitest, 44 Playwright e baseline de 100 tenants; reviews segurança e jornada P0-P3=0, acessibilidade P0-P2=0/P3=1 não bloqueante
Bloqueios: autorização específica para commit/push/Draft PR deste slice; Identity real, checkout/billing, staging/VPS/TLS, KMS/HA/restore/soak permanecem externos
Próxima ação: obter autorização explícita para publicar a branch da Issue #49; depois abrir Draft PR empilhada, atualizar Project e aguardar CI; não fazer merge/deploy
Project status:
  - Projeto: Infra
  - Status: Em review
  - Tipo: Feature
  - Prioridade: P1
  - Risco: Médio
  - Ambiente: Local
  - Próxima ação: Rodrigo autorizar commit, push e Draft PR da Issue #49
