---
name: model-routing
description: Escolher classe de modelo (fast/medium/strong/human) por tipo de tarefa e declarar no audit trail.
risk: Baixo
platforms: claude-code, codex, cursor
---

# Model Routing

## Quick path (uso rápido — tarefa óbvia)

1. Classificar tarefa (tabela abaixo).
2. Usar modelo correspondente.
3. Registrar classe usada em MEMORY_SESSION.md ou audit trail.

## Trigger — quando usar

- Ao iniciar QUALQUER tarefa — antes de chamar o modelo.
- Quando tarefa muda de escopo mid-sessão (reclassificar).
- Antes de delegar para subagente.
- Em handoff entre sessões (registrar classe usada).

## Pré-condição — verificar antes de iniciar

- [ ] Entendo o escopo da tarefa.
- [ ] Sei o ambiente (dev/staging/produção) — produção sempre exige human-in-the-loop.
- [ ] Tenho acesso aos modelos das 3 classes (fast, medium, strong).

## Workflow

### Passo 1: Classificar tarefa por tipo

| Tarefa | Classe | Modelos exemplo |
|--------|--------|-----------------|
| Resumir docs, listar arquivos, buscar/grep, format-preserving tweaks | **fast/cheap** | Haiku, GPT-4-mini |
| Bug simples, PR pequena, refactor isolado, debugger guiado | **medium** | Sonnet, GPT-4 |
| Arquitetura, auth, billing, produção, security review, design de migration | **strong** + human-in-the-loop | Opus, GPT-4-turbo |
| Deploy, migration de produção, rotação de secret, mudança de DNS/IAM | **humano executa**; agente apenas planeja e valida | (sem delegação ao modelo) |

### Passo 2: Aplicar regra de dúvida
Em dúvida entre duas classes: **escolher a superior**. Ex: dúvida entre medium e strong → strong. Otimizar custo só em tarefa confirmadamente simples.

### Passo 3: Considerar contexto adicional
- Tarefa em produção → sempre strong + human (mesmo que pareça simples).
- Tarefa em auth/billing/RLS/secrets → sempre strong.
- Tarefa em refactor que tocar >5 arquivos → medium ou strong (não fast).
- Tarefa repetitiva mecânica que parecer pequena: ainda assim, validação humana se afetar prod.

### Passo 4: Declarar classe escolhida
Registrar em:
- `docs/audit/session-current.jsonl` (se hook PostToolUse ativo)
- `docs/memory/MEMORY_SESSION.md` (campo "Modelo: <classe>")
- Comentário inline no início da sessão se manual

### Passo 5: Se subagente for usado
Passar a classe ao subagente. Subagente declara classe usada no relatório de status.

### Passo 6: Reclassificar em mid-tarefa
Se escopo aumenta (ex: "fix simples" virou refactor de auth):
- Parar
- Reclassificar
- Pode exigir aprovação adicional (se subiu para strong + human)
- Registrar mudança em MEMORY_SESSION

### Passo 7: Validar custo retrospectivamente (opcional)
Se sessão usou muitos tokens em strong onde fast bastaria: registrar aprendizado em MEMORY_LEARNINGS.md para tarefas similares no futuro.

## Checklist de validação — hard-assert

- [ ] Classe declarada antes de chamar modelo
- [ ] Tarefa em produção/auth/billing usa strong + human (nunca fast/medium sozinho)
- [ ] Deploy/migration nunca delegado a modelo (humano executa)
- [ ] Em dúvida, classe superior escolhida
- [ ] Classe registrada em audit trail ou MEMORY_SESSION

## Output esperado

Ao final da skill:
- Classe escolhida e justificada
- Registro persistente (audit trail + MEMORY_SESSION)
- Se reclassificou: motivo documentado

## Escalação — quando parar e pedir aprovação

- Tarefa cresce de medium para strong+human: aprovação antes de prosseguir.
- Tarefa de deploy/migration: SEMPRE humano executa — nunca delegar mesmo com forte modelo.
- Tarefa multi-projeto (Manu + Lili simultâneo): escalar — pode exigir coordenação.

## Risco e rollback

Risco: Baixo (uma má classificação custa tokens; raramente afeta correção).
Rollback: re-rodar com modelo apropriado. Custo: tokens extra + tempo.

## Exemplos

### Exemplo 1: Listar arquivos do repo
- Tarefa: `find . -name "*.ts" | wc -l`
- Classe: fast/cheap.
- Registro: Modelo: fast.

### Exemplo 2: Debug de bug simples em código de Manu
- Tarefa: investigar warning de pylint em 1 arquivo.
- Classe: medium.
- Registro: Modelo: medium.

### Exemplo 3: Refactor de auth middleware
- Tarefa: extrair lógica de JWT validation para módulo separado.
- Classe: strong + human (auth = sempre strong).
- Registro: Modelo: strong; Aprovação Rodrigo: pré-requisito.

### Exemplo 4: Deploy de Manu v1.4
- Tarefa: subir nova versão em produção.
- Classe: humano executa. Agente apenas planeja (rollback plan, healthz check, env validation).
- Registro: Modelo: humano (agente em planning mode).

### Exemplo 5: Tarefa cresce mid-sessão
- Início: "fix de typo em error message" → classificada fast.
- Descoberta: o typo está em error message gerada por código de billing.
- Reclassificação: medium → strong (billing = sempre strong).
- Registro em MEMORY_SESSION: "Reclassificado fast→strong por contexto billing".
