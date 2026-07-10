# MEMORY_DECISIONS.md — Decisões explícitas

Decisões técnicas/operacionais com motivo registrado. Append-only. Revisão prevista quando mudança no contexto.

## Schema obrigatório

```markdown
## Decisão [ID] — [título curto]
Data: [YYYY-MM-DD]
Decisor: Rodrigo
Contexto: [situação que gerou a decisão]
Opções consideradas:
  - A: [descrição] — trade-off: [...]
  - B: [descrição] — trade-off: [...]
  - C: [descrição] — trade-off: [...]
Decisão: [opção escolhida]
Motivo: [por que essa opção venceu]
Impacto: [o que muda no comportamento do time/agentes]
Revisão prevista: [data ou "quando X mudar"]
```

## Decisão 001 — GitHub Project obrigatório

Data: 2025-12-01
Decisor: Rodrigo
Contexto: Necessidade de rastrear todo trabalho de engenharia em painel único.
Opções consideradas:
  - A: GitHub Projects — trade-off: integração nativa, gratuito, limitado em automação
  - B: Linear — trade-off: mais robusto, custo extra, fora do GitHub
  - C: Notion como rastreamento — trade-off: flexível, mas não integra commits
Decisão: GitHub Projects.
Motivo: integração nativa com Issues/PRs; sem custo extra; suficiente para o tamanho atual.
Impacto: toda Issue/PR relevante deve estar no Project `Autonom.ia Dev` (https://github.com/users/autonom-ia/projects/3) com 7 campos preenchidos: Projeto, Status, Tipo, Prioridade, Risco, Próxima ação, Ambiente.
Revisão prevista: quando time crescer >5 engenheiros ou complexidade exigir multi-board.

## Decisão 002 — Harness v2.0 architecture (4 camadas)

Data: 2026-05-25
Decisor: Rodrigo
Contexto: Harness v1.0.2 tem stubs (hooks como README, skills de 20 linhas), sem observabilidade real, sem MCPs definidos. Precisa evoluir sem quebrar repos existentes.
Opções consideradas:
  - A: Evolutionary — aprofundar v1 em estrutura flat. Trade-off: baixa ruptura mas acoplamento crescente.
  - B: Plataforma Modular — 4 camadas (Core, Capabilities, Infra, Observatory) com platform-manifest enforcing portabilidade. Trade-off: mais design upfront, escalável.
  - C: Adaptive Harness — auto-assessment como feature central. Trade-off: mais complexo, depende de disciplina.
Decisão: B + C (modular + auto-assessment como feature de Layer 2/4).
Motivo: portabilidade Claude/Codex/Cursor exige contratos explícitos; auto-assessment evita drift.
Impacto: novos repos recebem 4 camadas; Codex/Cursor recebem Layer 1 + Layer 4 + 5 core skills as text in AGENTS.md; Claude Code recebe tudo.
Revisão prevista: pós-merge das 5 fases (F1-F5) e rollout em ≥3 repos.
