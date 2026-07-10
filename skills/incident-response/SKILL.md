---
name: incident-response
description: Detecção, contenção, rollback, comunicação e post-mortem para incidentes em produção.
risk: Alto
platforms: claude-code, codex, cursor
---

# Incident Response

## Quick path (uso rápido — apenas para incidentes BAIXO impacto e rollback pré-aprovado)

1. Confirmar sintoma (não agir em hipótese).
2. Executar rollback pré-aprovado se existir.
3. Validar healthz.
4. Registrar em MEMORY_INCIDENTS.md.

## Trigger — quando usar

- Serviço down ou degradado em produção (healthz != 200, error rate > threshold).
- Erro 5xx em endpoint crítico.
- Perda de dados detectada ou suspeita.
- Timeout em cascade entre serviços.
- Alerta de segurança (acesso suspeito, secret exposto, possível breach).
- Cliente reporta indisponibilidade ou bug crítico.

## Pré-condição — verificar antes de iniciar

- [ ] Sintoma é real (confirmado por log, métrica ou reprodução — não rumor).
- [ ] Identifiquei serviço(s) afetado(s).
- [ ] Sei se há rollback pré-aprovado (na PR original, runbook, ou MEMORY_INCIDENTS.md anterior).
- [ ] Sei o blast radius (1 serviço? múltiplos? cross-tenant?).

## Workflow

### Passo 1: Confirmar o sintoma — não agir em hipótese
- Reproduzir se possível (curl, dashboard, query)
- Capturar evidência (status code, timestamp, error message)
- Sem evidência = não é incidente confirmado. Investigar antes de mexer.

### Passo 2: Identificar serviços afetados
- Listar todos os serviços envolvidos (não só o que disparou alerta)
- Verificar dependências downstream (DB, queue, API externa)
- Cross-project safety: se afetar Manu E Lili simultaneamente, escalação dupla

### Passo 3: Conter — NÃO ampliar blast radius
- Não tentar fix complexo durante incidente
- Não fazer deploy "rápido" que ainda não foi testado
- Se houver feature flag para desligar a feature problemática: desligar PRIMEIRO

### Passo 4: Executar rollback pré-aprovado se existir
- Se a PR causadora do incidente tinha plano de rollback documentado: executar SEM nova aprovação (plano já está pré-aprovado)
- Comando exato, backup já existe → restore
- Alertar Rodrigo via STATUS_LOG.md em paralelo

### Passo 5: Se NÃO há rollback pré-aprovado — parar e pedir aprovação
- Reportar:
  - sintoma confirmado
  - blast radius
  - opções (rollback manual? hotfix? mitigação parcial?)
  - tempo estimado por opção
- Esperar decisão do Rodrigo. Não improvisar em produção.

### Passo 6: Coletar evidências durante contenção
- Logs do serviço afetado (últimos 30min)
- Logs de serviços dependentes
- Métricas (latency, error rate, throughput)
- Timestamp de cada ação tomada
- Salvar em `/tmp/incident-<YYYYMMDD-HHMMSS>/`

### Passo 7: Validar contenção via healthz
- healthz retorna 200 sustentado por > 2min
- Error rate volta ao baseline
- Sem cascade em serviços dependentes
- Cliente confirma recovery (se aplicável)

### Passo 8: Identificar root cause (após estabilizar)
- Não fazer durante o incidente — primeiro estabilizar
- Após recovery: aplicar skill `systematic-debugging` para encontrar causa raiz

### Passo 9: Fix definitivo em staging
- Implementar fix
- Validar em staging
- PR com plano de rollback

### Passo 10: Deploy do fix com aprovação
- Rodrigo aprova
- Aplicar `rollback-planning` skill
- Deploy com monitoramento ativo

### Passo 11: Post-mortem em MEMORY_INCIDENTS.md

```markdown
## Incidente [ID]
Data: [YYYY-MM-DD HH:MM]
Serviço afetado: [nome]
Sintoma: [o que quebrou]
Root cause: [causa raiz confirmada — não hipótese]
Resolução: [o que resolveu]
Rollback executado: sim | não
Duração: [HH:MM]
Aprendizado: [ID em MEMORY_LEARNINGS.md | "nenhum novo"]
```

### Passo 12: Aprendizado generalizável
- Se houve padrão de bug repetível: adicionar em MEMORY_LEARNINGS.md
- Se rollback não existia: criar plano para a feature antes de re-deploy
- Se monitoramento não detectou: adicionar alerta/dashboard

## Checklist de validação — hard-assert

- [ ] Sintoma confirmado com evidência (log, métrica, reprodução)
- [ ] Blast radius identificado
- [ ] Contenção executada (rollback, feature flag, ou mitigação)
- [ ] healthz estável por > 2min após contenção
- [ ] Evidências salvas em `/tmp/incident-<ts>/`
- [ ] Post-mortem em MEMORY_INCIDENTS.md
- [ ] Cliente notificado se incidente foi visível externamente
- [ ] Fix definitivo planejado e/ou implementado

## Output esperado

Ao final desta skill deve existir:
- Serviço estável (healthz 200, error rate baseline)
- Pasta `/tmp/incident-<ts>/` com evidências
- Entrada em MEMORY_INCIDENTS.md
- PR ou Issue para fix definitivo (se ainda não implementado)
- Aprendizado em MEMORY_LEARNINGS.md se generalizável

## Escalação — quando parar e pedir aprovação

- **Sempre** que incidente envolva:
  - Dados de cliente (PII, billing, sessão)
  - Auth/autorização (vazamento de permissão)
  - Múltiplos projetos simultaneamente
  - Backup destruído ou indisponível
- Quando rollback não é possível e a mudança causou o incidente: aprovação imediata do Rodrigo necessária para qualquer ação corretiva.
- Suspeita de breach/exfiltração: parar imediatamente, alertar Rodrigo, não tentar "investigar" sozinho (preservar evidência).

## Risco e rollback

Risco: Alto (ações em produção durante incidente podem agravar).
Rollback do rollback: se rollback piorou a situação — parar, escalar, não tentar undo do undo sozinho.

## Exemplos

### Exemplo 1: 502 em Manu após deploy
- Sintoma: healthz manu retorna 502 desde 14:32.
- Blast radius: Manu apenas (Lili não afetada).
- Rollback pré-aprovado existia (PR #102): `docker tag manu:v1.2.0 manu:rollback`.
- Executado às 14:35. healthz 200 às 14:36.
- Post-mortem: env var `OPENAI_API_KEY` foi sobrescrita por engano no deploy.
- Aprendizado: nunca omitir campo env em updateEnv (já registrado em M11b — não duplicar).

### Exemplo 2: Suspeita de breach
- Sintoma: log mostra acesso à API com token de usuário desconhecido.
- Ação: PARAR. Não fazer rollback. Não rotar secret (preserva evidência).
- Escalar imediatamente ao Rodrigo.
- Coletar logs intactos em `/tmp/incident-breach-<ts>/`.

### Exemplo 3: Lili lenta — sem incidente confirmado
- Sintoma reportado: "Lili está demorando para responder."
- Investigação: P95 latency 800ms (baseline: 500ms). Dentro do SLA.
- Decisão: NÃO é incidente. Abrir Issue de investigação, não disparar rollback.
