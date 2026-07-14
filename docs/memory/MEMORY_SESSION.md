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

## Sessão: 2026-07-14 10:40 — fechar replay transacional e capacidade da RC
Issue: #61
Branch: feat/61-release-candidate-e2e
PR: #62 (Draft, empilhada na #59)
Estado: em_progresso
Modelo: strong + human
Arquivos alterados: internal_requests.py; rotas asset/catalog/learning/platform/onboarding; asset_worker_runner.py; telemetry Python/TypeScript; testes API/auth/worker/storage/capacity; test-postgres-foundation.sh; docs/STATUS.md; docs/memory/MEMORY_SESSION.md
Comandos executados: test-storage-vertical; test-postgres-foundation; test-capacity-100-tenants; pytest completo; ruff/mypy/pip-audit; pnpm lint/typecheck/test/build/audit; test:e2e; validate-harness
Validação: dois reviews críticos finais GREEN para commit/PR (P0/P1/P2/P3 bloqueantes=0); PG 195 + boundary/worker 24; BFF 5; multi-seller 1; storage real API+auth+PG+S3 1; E2E 44 pass/6 skips; Harness 2.1.2 íntegro; capacidade passed com 107/487 waits (21,97%), zero erros, uma amostra isolada e artifact local inelegível por design
Bloqueios: novo SHA limpo ainda precisa CI e três runs de capacidade elegíveis; checkout/provider/Financial runtime/Neuro permanecem fora deste slice e sem produção
Próxima ação: commitar/push na Draft PR #62, acompanhar CI e repetir capacidade elegível no mesmo SHA; sem merge/deploy
Project status:
  - Projeto: Infra
  - Status: Em review
  - Tipo: Infra
  - Prioridade: P0
  - Risco: Médio
  - Ambiente: Local
  - Próxima ação: publicar a correção revisada na Draft PR #62 e certificar o novo SHA no CI; sem merge/deploy
