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

## Sessão: 2026-07-12 09:14 — capacidade de 100 admins e 1.000 buyers
Issue: #46
Branch: feat/46-capacity-1000-buyers
PR: não aberta
Estado: em_progresso
Modelo: strong + human
Arquivos alterados: seed/manifest de capacidade, testes Python, runner BFF, executor e documentação de estado/auditoria
Comandos executados: Ruff; pytest; ESLint; typecheck; verticais reais `scripts/test-capacity-100-tenants.sh`; scanner; inspeção de artifacts/teardown/host; Harness
Validação: local verde — 1.100 sessões, 1.000 library/player/progress/asset e negações separadas de produto/player/progress/asset; certificado final de 9.500 requests e 4.100 negações, zero 5xx/timeout/payload cruzado; BFF abaixo de 2 GiB; waits PostgreSQL abaixo do pico 5 e da razão de 25%, sem Lock/BufferPin/unclassified; headroom acima de 40%; teardown limpo; métricas exatas preservadas no artifact JSON; 126 Vitest, Python, lint/typecheck/build/audits verdes; publicação condicionada ao gate crítico final de proveniência
Bloqueios: autorização específica para commit/push/Draft PR; três runs CI para calibrar thresholds; staging/Identity/KMS/HA continuam externos
Próxima ação: obter autorização explícita para publicar PR empilhada sobre #45 e aguardar CI; não fazer merge/deploy
Project status:
  - Projeto: Infra
  - Status: Em review
  - Tipo: Infra
  - Prioridade: P0
  - Risco: Médio
  - Ambiente: Local
  - Próxima ação: Rodrigo autorizar commit/push/Draft PR; depois CI e review, sem merge
