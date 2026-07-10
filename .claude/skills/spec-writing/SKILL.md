---
name: spec-writing
description: Transformar ideia em spec executável com critérios de aceite testáveis.
risk: Baixo
platforms: claude-code, codex, cursor
---

# Spec Writing

## Quick path (uso rápido — apenas para risco BAIXO com contexto claro)

1. Coletar contexto do repo (arquivos relevantes, docs).
2. Escrever objetivo em 1 linha.
3. Listar critérios de aceite testáveis (3-5).
4. Identificar fora-de-escopo explícito.
5. Salvar em `docs/superpowers/specs/YYYY-MM-DD-<topic>.md`, commitar.

## Trigger — quando usar

- Nova feature ou capability sendo proposta.
- Refactor não trivial (>3 arquivos ou cross-module).
- Mudança de comportamento user-facing.
- Bug fix complexo que exige design (não fix óbvio).
- Sempre que houver ambiguidade entre stakeholder e implementação.

## Pré-condição — verificar antes de iniciar

- [ ] Entendo o problema (não só o pedido).
- [ ] Identifiquei stakeholder responsável pela decisão.
- [ ] Verifiquei se já existe spec relacionada (`docs/superpowers/specs/` ou docs/).
- [ ] Sei o nível de risco da mudança (Baixo / Médio / Alto).

## Workflow

### Passo 1: Coletar contexto
Ler arquivos relevantes do repo, docs existentes, commits recentes. Sem contexto, spec é fantasia.

### Passo 2: Perguntar uma coisa por vez
Quando há ambiguidade real, fazer UMA pergunta clara, com 2-3 opções e trade-offs. Esperar resposta antes da próxima pergunta.

### Passo 3: Propor 2-3 abordagens
Com trade-off real (custo, risco, prazo, complexidade). Recomendar uma com justificativa.

### Passo 4: Após aprovação da abordagem, escrever spec
Estrutura mínima:
- **Objetivo** (1 linha — o que muda no mundo)
- **Contexto** (situação atual e motivação)
- **Escopo** (o que entra)
- **Fora de escopo** (o que NÃO entra — explícito)
- **Critérios de aceite** (testáveis, hard-assert)
- **Riscos** (com mitigação)
- **Plano de rollback** (se aplicável)

### Passo 5: Self-review do spec
- [ ] Sem placeholders ("TBD", "TODO", "depois")
- [ ] Sem ambiguidade (cada termo tem uma interpretação)
- [ ] Critérios de aceite são testáveis (não "deve funcionar bem")
- [ ] Escopo enxuto (YAGNI — sem features hipotéticas futuras)

### Passo 6: Salvar em `docs/superpowers/specs/YYYY-MM-DD-<topic>.md`
Path absoluto baseado em data + slug curto.

### Passo 7: Commitar
```
git add docs/superpowers/specs/YYYY-MM-DD-<topic>.md
git commit -m "docs: spec — <topic>"
```

### Passo 8: Pedir review do Rodrigo antes de implementar
Spec sem revisão = chute formalizado.

## Checklist de validação — hard-assert

- [ ] Objetivo cabe em 1 linha
- [ ] Critérios de aceite são testáveis (cada um pode ser verificado por comando ou observação)
- [ ] Fora de escopo está explícito (não implícito)
- [ ] Riscos listados com mitigação
- [ ] Sem placeholders ou seções vagas
- [ ] Salvo em `docs/superpowers/specs/` com data
- [ ] Commitado em branch separada

## Output esperado

Ao final desta skill deve existir:
- Arquivo `docs/superpowers/specs/YYYY-MM-DD-<topic>.md` commitado
- Issue no GitHub linkando o spec (`Refs #N` quando implementação começar)

## Escalação — quando parar e pedir aprovação

- Spec afeta auth, billing, dados de cliente, RLS, tenant isolation.
- Spec exige mudança de schema de produção.
- Spec impacta múltiplos projetos (Manu, Lili, Hub2You etc.) simultaneamente.
- Stakeholder não está alinhado — não inventar requisitos.

## Risco e rollback

Risco: Baixo (spec é doc; só vira risco se implementação começar sem revisão).
Rollback: `git revert` do commit do spec. Re-discutir.

## Exemplos

### Exemplo 1: Spec de migração de Manu para outro DB
- Objetivo: migrar conversas de Manu de Postgres compartilhado para Postgres dedicado.
- Critérios de aceite: zero downtime; query latency P95 < 100ms; backup automático diário; rollback testado em staging.
- Fora de escopo: alterar schema; mudar lib do cliente.
- Riscos: bloqueio de write durante cutover (mitigação: dual-write em janela controlada).

### Exemplo 2: Spec de retry com backoff
- Objetivo: chamadas à API LLM com retry exponencial + jitter.
- Critérios de aceite: max 3 tentativas; backoff entre 1s e 30s; jitter aleatório ±20%; sem retry em erro 4xx (exceto 429).
- Fora de escopo: circuit breaker; rate limit local.
- Riscos: aumentar latência observada (mitigação: log p95 antes/depois).
