---
name: systematic-debugging
description: Debug sistemático baseado em evidência, não em hipótese ou achismo.
risk: Médio
platforms: claude-code, codex, cursor
---

# Systematic Debugging

## Quick path (uso rápido — apenas para risco BAIXO com contexto claro)

1. Reproduzir o bug com comando mínimo.
2. Registrar sintoma exato (mensagem, linha, contexto).
3. Identificar causa raiz com evidência (não hipótese).
4. Fix mínimo na causa raiz — sem refatorar além.
5. Rodar teste de reprodução + suite. Commitar.

## Trigger — quando usar

- Bug reportado por usuário, sistema ou agente.
- Erro no CI ou em produção.
- Comportamento inesperado: output errado, latência alta, falha intermitente.
- Agente produz resultado errado de forma repetida.

## Pré-condição — verificar antes de iniciar

- [ ] `git status` limpo (não misturar mudanças pendentes com debug).
- [ ] Tenho mensagem de erro, log ou stack trace concreto (não relato vago).
- [ ] Sei qual era o comportamento esperado vs. o observado.
- [ ] Ambiente identificado (dev / staging / produção) — produção exige aprovação para qualquer write.

## Workflow

### Passo 1: Reproduzir o bug de forma isolada
Encontrar o menor comando, request ou input que reproduz o problema. Sem reprodução, não há debug — só hipótese.

### Passo 2: Registrar sintoma exato em MEMORY_SESSION.md
- mensagem de erro literal (copy/paste, não paráfrase)
- arquivo:linha
- contexto da chamada
- timestamp se for log de produção

### Passo 3: Listar 3-5 hipóteses de causa raiz
Ordenar por probabilidade. Cada hipótese deve ser falsificável.

### Passo 4: Para cada hipótese, definir evidência que confirma ou refuta
Pergunta: "Se essa hipótese for verdadeira, o que eu deveria observar?"

### Passo 5: Executar teste mais rápido/barato primeiro
grep, log inspection, variável, env var — antes de subir debugger ou reproduzir em outro ambiente.

### Passo 6: Registrar resultado de cada teste
Nunca avançar para próxima hipótese sem evidência registrada da anterior.

### Passo 7: Eliminar hipóteses refutadas
Marcar como descartada com motivo. Mantém histórico — útil se voltar a aparecer.

### Passo 8: Aprofundar hipótese mais provável até causa raiz
Causa raiz = ponto onde o fix elimina o bug definitivamente, não onde o sintoma some.

### Passo 9: Validar causa raiz com evidência (não "parece ser")
Reproduzir o estado que confirma a causa. Sem isso, fix é chute.

### Passo 10: Propor fix mínimo que resolve apenas essa causa
Sem refatorar além. Sem "já que está aqui, vou também". Scope = bug fix.

### Passo 11: Implementar fix em branch separada
`fix/<descrição-curta>` ou `bug/<issue-N>`.

### Passo 12: Rodar o teste de reprodução do Passo 1
Bug deve sumir. Se não sumir, fix está errado — voltar ao Passo 8.

### Passo 13: Rodar suite completa
Garantir zero regressão.

### Passo 14: Atualizar MEMORY_SESSION.md e MEMORY_LEARNINGS.md
Se aprendizado é generalizável (ex: padrão de bug em retry sem backoff), registrar.

## Checklist de validação — hard-assert

- [ ] Bug reproduzido de forma isolada antes de qualquer fix
- [ ] Causa raiz tem evidência registrada (não hipótese)
- [ ] Fix toca apenas a causa raiz (sem escopo extra)
- [ ] Teste de reprodução passa após fix
- [ ] Suite completa passa (sem regressão)
- [ ] MEMORY_SESSION.md atualizado
- [ ] Se bug afetou produção: rollback plan documentado

## Output esperado

Ao final desta skill deve existir:
- Branch `fix/...` com commit atômico do fix
- PR aberta linkando Issue do bug (`Refs #N` ou `Closes #N`)
- Entrada em MEMORY_LEARNINGS.md se o aprendizado for generalizável
- MEMORY_SESSION.md com causa raiz documentada

## Escalação — quando parar e pedir aprovação

- Causa raiz aponta para auth, billing, RLS, tenant isolation, ou produção.
- Fix exige migração de banco de produção.
- Bug afeta dados de cliente (PII, billing, sessão).
- Fix exige downtime ou rollback de release recente.

Não decidir sozinho. Reportar evidência + opções ao Rodrigo.

## Risco e rollback

Risco: Médio (fix em código pode introduzir regressão).
Rollback: `git revert <commit-do-fix>` e voltar a investigar. Sem rollback de produção sem aprovação.

## Exemplos

### Exemplo 1: Manu não responde às 22h
- Sintoma: usuário relata silêncio do agente após 22h.
- Hipóteses: (a) cron parado, (b) rate limit, (c) timezone, (d) DB lento.
- Teste mais barato: grep log de erro 22h+.
- Evidência: status 429 da API LLM nos últimos 30 dias após 22h.
- Causa raiz: chamada sem backoff em horário de pico.
- Fix: retry exponencial com jitter.
- Validação: teste de stress simula 100 req/min → sem 429.

### Exemplo 2: Migration falha em staging
- Sintoma: `ALTER TABLE` trava por 5min e timeout.
- Hipóteses: (a) lock holder, (b) tabela grande sem index, (c) FK pendente.
- Evidência: `pg_stat_activity` mostra lock de transação aberta há 4h.
- Causa raiz: webhook em loop segurando transação.
- Fix: matar transação + corrigir handler do webhook (idempotência).
- Escalação: produção exige aprovação antes de aplicar.
