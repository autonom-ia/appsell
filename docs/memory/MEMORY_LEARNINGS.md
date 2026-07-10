# MEMORY_LEARNINGS.md — Aprendizados com evidência

Aprendizados generalizáveis observados em incidentes, sessões ou revisões. Append-only. Cada aprendizado precisa de evidência concreta (não achismo).

## Schema obrigatório

```markdown
## Aprendizado [ID]
Data: [YYYY-MM-DD]
Origem: incidente | sessão | revisão | code review
Projeto: [Manu | Lili | Hub2You | Infra | etc.]
Evidência: [o que foi observado — fato, não interpretação. Linkar log, commit, Issue se aplicável]
Aprendizado: [o que muda no comportamento — regra acionável]
Confiança: alta | média | baixa
Aplicar em: [escopo — onde a regra vale]
Revisado por Rodrigo: sim | não
Incorporado em: [arquivo onde virou regra — MEMORY_CORE, .claude/rules/, ou "ainda candidato"]
```

## Regra para promover Aprendizado → Regra permanente

1. Confiança "alta" + observado em ≥2 incidentes/sessões → candidato a MEMORY_CORE
2. Confiança "média" → fica em MEMORY_LEARNINGS aguardando próxima observação
3. Confiança "baixa" → marcado para revisão; pode virar nota informativa

## Aprendizado 001 — Exemplo (substituir/adicionar)

Data: [YYYY-MM-DD]
Origem: [ex: incidente 2026-05-22]
Projeto: [ex: Lili]
Evidência: [ex: destroyService no endpoint Easypanel afetou n8n_db compartilhado, derrubando 2 projetos]
Aprendizado: [ex: nunca executar destructive op em endpoint compartilhado sem backup confirmado e aprovação explícita]
Confiança: alta
Aplicar em: [ex: toda operação Easypanel em VPS multi-tenant]
Revisado por Rodrigo: sim
Incorporado em: MEMORY_CORE Regra 001
