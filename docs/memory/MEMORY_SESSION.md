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

## Sessão: 2026-07-10 22:03 — shell Studio mobile-first e jornadas seller/buyer
Issue: #22
Branch: feat/22-studio-mobile-shell
PR: não aberta
Estado: em_progresso
Modelo: strong + human
Arquivos alterados: app público/protegido, proxy, componentes, design system, projeções demo, testes, STATUS/PROGRESS/DECISIONS/audit
Comandos executados: validate-harness/doctor; pnpm lint/typecheck/test/build/test:e2e/test:e2e:production; Playwright CLI/screenshot; reviews críticos
Validação: local verde — Harness completo com drift 2.0.2→2.1.2; lint/typecheck/build; 55 unitários; 17 browser + 1 skip de matriz; 1 production local; três reviews P0-P3=0
Bloqueios: CI remoto e aprovação de merge; Identity real #13, KMS #19 e produção continuam bloqueados
Próxima ação: commit/push, abrir PR draft da #22, atualizar Project e aguardar CI
Project status:
  - Projeto: AppSell
  - Status: Em review
  - Tipo: Feature
  - Prioridade: P1
  - Risco: Médio
  - Ambiente: Dev
  - Próxima ação: abrir PR draft e executar CI remoto
