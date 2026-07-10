---
name: memory-compaction
description: Compactar contexto sem perder estado crítico (issue, branch, PR, próxima ação).
risk: Médio
platforms: claude-code, codex, cursor
---

# Memory Compaction

## Quick path (uso rápido — apenas para risco BAIXO com contexto claro)

1. Listar campos não-negociáveis: issue, branch, PR, próxima ação.
2. Atualizar MEMORY_SESSION.md com estado completo.
3. Atualizar PROGRESS.md e STATUS.md.
4. Compactar.
5. Após compactação, ler MEMORY_SESSION.md para verificar continuidade.

## Trigger — quando usar

- Contexto restante ~20% (regra Rodrigo #5).
- Handoff de sessão (entre agentes ou sessões diferentes).
- Mudança de tarefa dentro da mesma branch.
- Pré-Stop event (hook stop).

## Pré-condição — verificar antes de iniciar

- [ ] Sei qual issue, branch, PR são ativas.
- [ ] Sei o estado atual da tarefa (em_progresso | bloqueado | concluído).
- [ ] Sem ações destrutivas pendentes (não compactar durante deploy/migration).
- [ ] `docs/memory/` existe (se não, criar via `apply-harness-to-repo.sh`).

## Workflow

### Passo 1: Listar o que NÃO pode perder
- Objetivo da tarefa (1 linha)
- Issue (#N)
- Branch atual
- PR (#N) ou "não aberta"
- Arquivos alterados
- Comandos executados (críticos)
- Decisões do Rodrigo
- Resultados de validação
- Bloqueios abertos
- Rollback plan se existir
- Próxima ação exata

### Passo 2: Listar o que PODE descartar
- Logs repetidos
- Outputs longos já resumidos
- Conversas sociais
- Hipóteses descartadas (manter motivo, não detalhe)
- Tentativas abandonadas (manter aprendizado se houver)

### Passo 3: Atualizar `docs/memory/MEMORY_SESSION.md` com schema completo

```markdown
## Sessão: [data] — [objetivo em uma linha]
Issue: #N
Branch: [nome]
PR: #N (ou "não aberta")
Estado: em_progresso | bloqueado | concluído
Arquivos alterados: [lista]
Comandos executados: [lista crítica]
Validação: [resultado ou "não rodou — motivo"]
Bloqueios: [lista com dono e ação esperada | "nenhum"]
Próxima ação: [exata — o que fazer ao retomar]
Project status: [campo por campo]
```

### Passo 4: Atualizar `docs/PROGRESS.md`
Linha temporal do que foi feito na sessão.

### Passo 5: Atualizar `docs/STATUS.md`
Estado do projeto (não da sessão) — health indicators.

### Passo 6: Validar nenhum bloqueio aberto não documentado
Grep `MEMORY_SESSION.md` por "TODO" ou "PENDENTE" — todos devem ter dono e ação.

### Passo 7: Executar compactação
Disparar comando de compactação do agente runtime (ou aceitar compactação automática).

### Passo 8: APÓS compactação, ler `MEMORY_SESSION.md` imediatamente
Verificar que continuidade é possível: issue, branch, próxima ação ainda fazem sentido.

## Checklist de validação — hard-assert

- [ ] Issue preservada e correta
- [ ] Branch preservada e correta
- [ ] PR preservada (número ou "não aberta")
- [ ] Próxima ação exata (suficiente para retomar)
- [ ] Sem bloqueio aberto não documentado
- [ ] Project status preenchido (todos os campos)
- [ ] Rollback plan preservado se aplicável

## Output esperado

Ao final desta skill deve existir:
- `docs/memory/MEMORY_SESSION.md` atualizado com estado completo
- `docs/PROGRESS.md` com entrada do que foi feito
- `docs/STATUS.md` com health snapshot
- Contexto compactado sem perder os campos não-negociáveis

## Escalação — quando parar e pedir aprovação

- Bloqueio aberto sem dono claro: pedir alocação ao Rodrigo antes de compactar.
- Estado da sessão é "trabalho em produção pendente": parar — não compactar; finalizar primeiro.
- Detectou conflito entre MEMORY_SESSION e Project (status divergente): resolver antes.

## Risco e rollback

Risco: Médio (compactação perde detalhes — perda de continuidade se schema mal preenchido).
Rollback: re-ler PROGRESS.md e Git history para reconstruir. Mais demorado mas possível.

## Exemplos

### Exemplo 1: Sessão de 4h no harness v2 F1
- Issue: #5 (épico harness v2)
- Branch: feature/harness-v2-f1-core-infra
- PR: #3
- Estado: em_progresso
- Próxima ação: continuar F2 (skills profundas) em nova conversa após merge de F1.
- Bloqueios: nenhum.
- Project status: PR aberta, Tipo Infra, P1, Risco Baixo, Ambiente Dev.

### Exemplo 2: Handoff entre agentes (Claude → Codex)
- Issue: #42 (bug retry sem backoff)
- Branch: fix/retry-backoff
- PR: não aberta (ainda em hipótese)
- Estado: bloqueado
- Bloqueios: aguarda confirmação Rodrigo se rate limit é 100req/min ou 1000req/min.
- Próxima ação: ao desbloquear, implementar retry exponencial com max 3 tentativas.
