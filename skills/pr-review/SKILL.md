---
name: pr-review
description: Revisar PR crítico com checklist de 15 pontos. Separa bugs, riscos, scope creep e secrets.
risk: Médio
platforms: claude-code, codex, cursor
---

# PR Review

## Quick path (uso rápido — apenas para risco BAIXO com contexto claro)

1. Ler diff completo (não só primeiros arquivos).
2. Buscar secrets/credenciais hardcoded.
3. Verificar testes adicionados/atualizados.
4. Conferir scope (PR não toca além do prometido).
5. Aprovar com finding-zero OU listar findings por severidade.

## Trigger — quando usar

- PR aberta aguardando review.
- Antes de merge — review é gate inegociável.
- Pull em produção ou release branch.
- Mudança que toca auth, billing, webhooks, schema.

## Pré-condição — verificar antes de iniciar

- [ ] PR linkada a Issue (`Refs #N` ou `Closes #N`).
- [ ] CI verde ou com warnings explicáveis.
- [ ] Template da PR preenchido (sumário, test plan, riscos).
- [ ] Branch atualizada com base (não conflito visível).

## Workflow

### Passo 1: Ler PR description
Sumário, motivação, test plan, riscos. Sem isso, review é vazio.

### Passo 2: Ler diff linha a linha (não pular)
`gh pr diff <N>` ou tooling equivalente. Não confiar em "parece OK".

### Passo 3: Aplicar checklist de 15 pontos (abaixo)
Cada item é hard-assert. Sem soft-pass.

### Passo 4: Classificar findings por severidade
- **BLOCKER**: merge proibido até resolver (security, regressão, dado em risco).
- **MAJOR**: deve resolver antes de merge (bug funcional, escopo errado, secret).
- **MINOR**: nice-to-fix (style, perf marginal, comment).
- **INFO**: observação para futuro (não bloqueia).

### Passo 5: Comentar inline na PR para findings
- Cite linha exata
- Explique o problema (não só "isso está errado")
- Sugira fix concreto quando possível

### Passo 6: Decidir
- Zero finding ou só INFO → aprovar (sem auto-merge)
- BLOCKER → REQUEST CHANGES
- MAJOR → COMMENT com pedido de fix
- MINOR → COMMENT informativo

### Passo 7: Atualizar Project
Status: `Em review` → `Ajustes` (se findings) ou `Aprovada` (se zero).

## Checklist de validação — hard-assert (15 pontos)

1. [ ] **Secrets**: sem API key, token, password, cookie hardcoded
2. [ ] **`.env` / credenciais**: não commitadas
3. [ ] **Scope**: PR toca apenas o que está na descrição (sem refactor escondido)
4. [ ] **Testes**: novos testes para nova funcionalidade; testes ajustados para mudança
5. [ ] **Regressão**: PR não quebra fluxo existente (testes manuais ou automatizados)
6. [ ] **Migrations**: têm down-migration se aplicável; backup plan documentado
7. [ ] **Auth/RLS**: sem enfraquecer permissões, tenant isolation, RLS
8. [ ] **Webhooks**: validação de assinatura + idempotência se aplicável
9. [ ] **Error handling**: erros tratados conscientemente (não swallow)
10. [ ] **Logs**: sem log de secret/PII; com contexto suficiente para debug
11. [ ] **Performance**: sem N+1 query, sem loop síncrono em chamada externa, sem busca full-table sem index
12. [ ] **Docs**: README, env.example, runbook atualizados se comportamento mudou
13. [ ] **Linked Issue**: PR linkada a Issue com `Refs #N` ou `Closes #N`
14. [ ] **Rollback plan**: documentado quando aplicável (deploy, migration, infra)
15. [ ] **CI verde**: ou warnings explicáveis (não falhas reais ignoradas)

## Output esperado

Ao final desta skill deve existir:
- Comentários inline na PR para cada finding
- Review summary com classificação (Approve / Request changes / Comment)
- Project atualizado com status correto
- Issue (se zero finding) marcada para próxima etapa de release

## Escalação — quando parar e pedir aprovação

- Finding de **segurança**: parar, não comentar light — escalar ao Rodrigo.
- Finding em **auth/billing/RLS**: bloquear PR + escalar.
- Suspeita de exfiltração de dados ou backdoor: parar e alertar imediatamente.
- PR para `main` sem aprovação do Rodrigo no path crítico: bloquear.

## Risco e rollback

Risco: Médio (review fraca permite bug em produção).
Rollback: se PR foi merged com finding crítico descoberto depois: `git revert` no merge + comunicar imediatamente.

## Exemplos

### Exemplo 1: PR que adiciona retry mas remove validação de assinatura
- Diff mostra: retry exponencial no webhook handler, mas remove `verify_signature()`.
- Finding: BLOCKER em #7 e #8 (webhook sem validação = aceita request forjada).
- Ação: REQUEST CHANGES + escalar ao Rodrigo (segurança).

### Exemplo 2: PR de bug fix com refactor escondido
- Descrição: "fix off-by-one em paginação".
- Diff: fix correto + reorganização de 8 arquivos não relacionados.
- Finding: MAJOR em #3 (scope creep).
- Ação: pedir split em 2 PRs (fix + refactor).

### Exemplo 3: PR limpa
- Diff: 12 linhas, testes adicionados, sem secret, scope correto.
- Findings: zero (ou só INFO sobre comment style).
- Ação: Approve sem auto-merge. Aguardar Rodrigo.
