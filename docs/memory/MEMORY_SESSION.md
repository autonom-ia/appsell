# MEMORY_SESSION.md — Estado da tarefa atual

Atualizado durante a sessão para permitir compactação e retomada sem repetir a
investigação.

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

## Sessão: 2026-08-24 — consolidar documentação e handoff do estado real

Issue: #202
Branch: `codex/202-docs-production-handoff`
PR: #203
Estado: concluído
Modelo: strong + humano
Arquivos alterados: `README.md`, `docs/PRODUCTION_CURRENT.md`,
`docs/runbooks/PRODUCTION_RELEASE_APPSSELL.md`, `docs/STATUS.md`,
`docs/PROGRESS.md`, `docs/DECISIONS.md`, `docs/architecture/README.md`,
`docs/architecture/AUTHORIZATION_MATRIX.md`,
`docs/architecture/DATA_MODEL.md`,
`docs/architecture/MIGRATION_AND_ROLLBACK.md`,
`docs/architecture/DECISION_REGISTER.md`,
`docs/implementation/seller-publish-ux/progress.md`,
`deploy/easypanel-native/README.md`,
`deploy/easypanel-native/backup/README.md`,
`docs/runbooks/BACKUP_RESTORE_SELL.md`,
`docs/runbooks/generic-webhook-ingress.md`,
`docs/runbooks/monetizacao-96-deploy.md`,
`docs/runbooks/stripe-143-live-checkout.md`,
`docs/plans/EXECUTIVE_PLAN.md`, `docs/plans/TECHNICAL_PLAN.md`,
`deploy/easypanel-native/backup/README.md`,
`deploy/easypanel-native/backup/lib-backup.sh`,
`deploy/easypanel-native/backup/backup-native.sh`,
`docs/memory/MEMORY_PROJECT.md`, `docs/memory/MEMORY_SESSION.md`
Comandos executados: criação da Issue #202 e item no Project #3; criação da
branch a partir de `origin/main`; leitura e atualização dos documentos; ainda
não houve merge, deploy ou alteração de runtime nesta sessão.
Validação: `git diff --check` PASS; arquivos canônicos presentes; referências
de SHA/migration/Issue conferidas; testes de aplicação não rodados porque a
mudança é somente documental.
Bloqueios: nenhum técnico; merge/deploy não fazem parte desta autorização.
Próxima ação: revisar a PR #203; após aprovação explícita, fazer merge conforme
o fluxo do projeto. Nenhum deploy faz parte desta PR.

Project status:
  - Projeto: Appsell
  - Status: PR aberta
  - Tipo: Docs
  - Prioridade: P1
  - Risco: Baixo
  - Ambiente: Produção
  - Próxima ação: revisar e aprovar a PR #203
