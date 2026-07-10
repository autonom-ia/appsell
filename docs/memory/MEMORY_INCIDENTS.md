# MEMORY_INCIDENTS.md — Histórico de incidentes

Preenchido pelo agente (via skill `incident-response`) após cada incidente significativo. Append-only.

## Schema obrigatório

```markdown
## Incidente [ID]
Data: [YYYY-MM-DD HH:MM UTC]
Serviço afetado: [nome do serviço/projeto]
Sintoma: [o que quebrou — observação concreta, não interpretação]
Detectado por: [alerta automático | usuário | agente | review]
Blast radius: [escopo — 1 serviço | múltiplos | cross-tenant | dados de cliente]
Root cause: [causa raiz confirmada — não hipótese]
Resolução: [o que resolveu]
Rollback executado: sim | não
Duração: [HH:MM do sintoma à resolução]
Aprendizado: [ID em MEMORY_LEARNINGS.md | "nenhum novo"]
Post-mortem: [link doc | "inline acima"]
```

## Política

1. Todo incidente em produção deve gerar entrada aqui antes da sessão encerrar
2. Se incidente envolveu dado de cliente, billing ou auth → marcar com flag `Severidade: Crítica`
3. Se rollback foi executado → vincular ao plano de rollback original (PR ou doc)
4. Aprendizado generalizável → criar entrada em MEMORY_LEARNINGS.md e linkar aqui

## Incidentes registrados

<!-- Entradas novas vão abaixo. Append-only — nunca editar entrada existente. Apenas anotar correção em entrada nova referenciando o ID. -->

## Incidente 2026-05-22-001 — Lili + n8n perdidos

Data: 2026-05-22 (horário não registrado precisamente — adicionado retroativamente)
Serviço afetado: Lili (persona) + n8n_db (banco compartilhado)
Sintoma: serviços Lili e n8n indisponíveis após operação em endpoint TRPC Easypanel
Detectado por: agente (durante operação)
Blast radius: cross-project (2 projetos derrubados por op em endpoint compartilhado)
Root cause: execução de operação destrutiva (`destroyService`/`deleteService`) em endpoint Easypanel que afetou recursos compartilhados (banco `n8n_db`)
Resolução: restore via snapshot Hostinger
Rollback executado: sim (via snapshot, não via plano pré-aprovado — não existia)
Duração: não registrada precisamente
Aprendizado: gerou Regra 001 em MEMORY_CORE — "Sem destructive op em endpoint Easypanel compartilhado sem aprovação + backup"
Post-mortem: registrado retroativamente neste arquivo; gerou regras universais Rodrigo #2 (Cross-project safety) no CLAUDE.md global
Severidade: Crítica
