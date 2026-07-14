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

## Sessão: 2026-07-14 08:10 — implementar webhook genérico e outbox durável
Issue: #63
Branch: feat/63-generic-webhook-ingress
PR: não aberta
Estado: em_progresso
Modelo: strong + human
Arquivos alterados: migration 0012; API webhook adapter/routes/secrets; rota e proxy BFF; testes unitários/PG/BFF; compose/env; docs
Comandos executados: pytest/ruff/mypy; vitest/typecheck/eslint; test-postgres-foundation; inspeção do artifact CI #62
Validação: gate isolado exit 0 com 193 PG/API, 6 BFF, multi-seller 1/1 e artifact scan verde; 272 Vitest + 9 skips condicionais, lint/typecheck/build/audits verdes; Harness 2.1.2 sem faltas; reviews AppSec, PostgreSQL e contrato GREEN P0=P1=P2=P3=0
Bloqueios: nenhum bloqueio local; provider/Financial/Neuro/Identity/staging dependem de ambientes e contratos isolados em slices posteriores
Próxima ação: commit/push e abrir Draft PR empilhada sobre #62; sem merge/deploy
Project status:
  - Projeto: Autonom.ia Dev
  - Status: Em review
  - Tipo: Feature
  - Prioridade: P0
  - Risco: Alto
  - Ambiente: Local
  - Próxima ação: publicar Draft PR empilhada; sem merge/deploy
