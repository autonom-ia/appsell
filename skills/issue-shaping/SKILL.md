---
name: issue-shaping
description: Transformar problema em Issue rastreável com critérios de aceite testáveis e campos de Project preenchidos.
risk: Baixo
platforms: claude-code, codex, cursor
---

# Issue Shaping

## Quick path (uso rápido — bugs óbvios ou tarefas claras)

1. Título imperativo curto (< 60 chars).
2. Descrição: contexto + comportamento atual vs. esperado.
3. Critérios de aceite (3-5 itens testáveis).
4. Risco (Baixo/Médio/Alto) e Ambiente.
5. `gh issue create` + adicionar ao Project.

## Trigger — quando usar

- Nova funcionalidade a planejar.
- Bug report (interno ou de cliente).
- Débito técnico identificado em PR review.
- Incidente que gerou aprendizado (Issue para fix permanente).
- Ideia precisa virar trabalho rastreável.

## Pré-condição — verificar antes de iniciar

- [ ] Entendi o problema (não só o sintoma reportado).
- [ ] Verifiquei se já existe Issue para o mesmo problema (`gh issue list --search "<keywords>"`).
- [ ] Sei qual repo (não criar Issue cross-repo sem confirmar).
- [ ] Sei o projeto/persona afetada (Manu | Lili | Hub2You | Infra etc.).

## Workflow

### Passo 1: Título imperativo claro
- Começa com verbo: "Adicionar...", "Corrigir...", "Refatorar...", "Investigar..."
- Sem prefixo de tipo (Project tem campo Tipo separado)
- < 60 chars

### Passo 2: Descrição estruturada

```markdown
## Contexto
[Situação atual — onde isso aparece, quando, frequência]

## Comportamento atual
[O que acontece hoje — fato observado, não interpretação]

## Comportamento esperado
[O que deveria acontecer]

## Critérios de aceite
- [ ] [Asserção testável 1]
- [ ] [Asserção testável 2]
- [ ] [Asserção testável 3]

## Dependências
- Issues bloqueadoras: #N, #M
- Serviços/sistemas tocados: [lista]

## Risco
- Nível: Baixo | Médio | Alto
- Mitigação: [se aplicável]

## Ambiente
Local | Dev | Staging | Produção
```

### Passo 3: Critérios de aceite testáveis
Cada um deve ser verificável por:
- Comando: `curl ... | jq .x == "value"` ✅
- Observação binária: "Endpoint X retorna 200 em condição Y" ✅
- Métrica: "P95 latência < 200ms" ✅

NÃO testável:
- "Deve funcionar bem" ❌
- "Usuário satisfeito" ❌
- "Performance melhor" ❌

### Passo 4: Estimar risco
- **Baixo**: docs, refactor isolado, feature flag-gated.
- **Médio**: muda comportamento user-facing, requer migration reversível.
- **Alto**: produção, auth, billing, dados de cliente, migration irreversível.

### Passo 5: Identificar dependências
- Issues bloqueadoras (`Blocked by #N`)
- Issues relacionadas (`Refs #M`)
- Sistemas externos (API, SDK, contrato com terceiro)

### Passo 6: Criar Issue
```bash
gh issue create \
  --title "<título imperativo>" \
  --body "<descrição estruturada>" \
  --label "<tipo>" \
  --assignee "<owner>"
```

### Passo 7: Adicionar ao Project
Via skill `project-update`. Preencher 7 campos: Projeto, Status, Tipo, Prioridade, Risco, Próxima ação, Ambiente.

### Passo 8: Linkar em conversa/PR original
Se Issue veio de discussão em PR ou Slack: comentar lá com link da Issue para preservar histórico.

## Checklist de validação — hard-assert

- [ ] Título é imperativo e < 60 chars
- [ ] Descrição tem todas as 6 seções (Contexto, Atual, Esperado, Aceite, Deps, Risco, Ambiente)
- [ ] Critérios de aceite são testáveis (cada um verificável por comando ou observação binária)
- [ ] Risco preenchido (não "TBD")
- [ ] Não é duplicata (verificado via search)
- [ ] Adicionada ao Project com 7 campos
- [ ] Owner ou "unassigned" (mas decidido, não esquecido)

## Output esperado

Ao final desta skill deve existir:
- Issue no GitHub com descrição estruturada
- Item no Project com campos preenchidos
- Link da Issue postado onde ela foi originada (PR, conversa, doc)

## Escalação — quando parar e pedir aprovação

- Issue afeta múltiplos repos: pedir orientação ao Rodrigo (criar mestre + sub-issues?).
- Issue tem ambiguidade que muda escopo: parar, perguntar, não chutar.
- Issue de incidente em produção: criar Issue com flag de urgência E escalar paralelamente.

## Risco e rollback

Risco: Baixo (Issue é metadata; sem efeito em código/prod).
Rollback: `gh issue close <N>` ou edit do título/body. Histórico preservado.

## Exemplos

### Exemplo 1: Bug em Manu
- Título: "Corrigir retry em Manu sem backoff causando 429"
- Contexto: usuário reportou silêncio às 22h em 5 dias da semana passada.
- Atual: chamadas falham com 429 e agente para.
- Esperado: retry exponencial com backoff + jitter; max 3 tentativas; sem erro user-facing.
- Critérios: (a) log mostra retry attempt 1,2,3; (b) sem 429 em stress test 100req/min; (c) error rate < 0.1% no horário.
- Risco: Médio (toca handler de API LLM).
- Ambiente: Produção.

### Exemplo 2: Refactor de débito técnico
- Título: "Extrair lógica de templating de Manu para módulo separado"
- Contexto: file `manu/agent.py` tem 1200 linhas misturando templating, LLM call e logging.
- Atual: alterar template exige tocar o arquivo todo (alto risco).
- Esperado: módulo `manu/templating.py` isolado, com testes unitários.
- Critérios: (a) `agent.py` < 400 linhas; (b) `templating.py` tem >80% coverage; (c) zero regressão em testes E2E.
- Risco: Médio (refactor de produção).
- Ambiente: Dev → Staging → Produção (rollout faseado).

### Exemplo 3: Feature pedida pelo Rodrigo
- Título: "Adicionar export de conversas em PDF no painel do cliente"
- Critérios: (a) botão "Exportar PDF" em /conversations/:id; (b) PDF inclui mensagens + timestamps + persona; (c) tempo de geração < 5s para 100 msgs.
- Risco: Baixo (feature isolada, sem afetar core).
- Ambiente: Dev (rollout via feature flag).
