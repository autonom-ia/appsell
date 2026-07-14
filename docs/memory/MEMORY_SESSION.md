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

## Sessão: 2026-07-14 07:55 — corrigir certificado de capacidade da RC
Issue: #61
Branch: feat/61-release-candidate-e2e
PR: #62 (Draft, empilhada na #59)
Estado: em_progresso
Modelo: strong + human
Arquivos alterados: lib/capacity-postgres-headroom.ts; scripts/test-capacity-100-tenants.sh; tests/bff-capacity-100-tenants.test.ts; tests/capacity-postgres-headroom.test.ts; docs/STATUS.md; docs/memory/MEMORY_SESSION.md
Comandos executados: gh run rerun/cancel; vitest headroom/sampler; eslint direcionado; bash -n; test-capacity-100-tenants; pnpm lint/typecheck/test/build/audit
Validação: review crítico RED/P1 no SHA 5d8933b; correção local com 266 testes e 8 skips condicionais, vertical 100 admins/1.000 buyers, lint/typecheck/build/audit verdes; pool API 5 vindo do contrato Python; 0 amostras de pool completo; wait 63/458; zero erro funcional
Bloqueios: novo SHA ainda precisa review crítico e três runs CI verdes; checkout/provider/Financial runtime/Neuro permanecem fora deste slice e sem produção
Próxima ação: commitar/push, obter review verde e reiniciar três runs CI no mesmo SHA
Project status:
  - Projeto: Infra
  - Status: Ajustes
  - Tipo: Infra
  - Prioridade: P0
  - Risco: Médio
  - Ambiente: Local
  - Próxima ação: validar e publicar correção do capacity gate na Draft PR #62; sem merge/deploy
