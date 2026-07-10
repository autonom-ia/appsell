---
name: security-review
description: Revisão de segurança hard-assert para PRs que tocam auth, billing, webhooks, RLS, secrets, tenant isolation. Zero soft pass.
risk: Alto
platforms: claude-code, codex, cursor
---

# Security Review

## Quick path (uso rápido — NUNCA. Security não tem quick path.)

Security review é sempre Full workflow. Sem exceção.

## Trigger — quando usar

- PR que toca código de auth (login, signup, password reset, session, JWT, OAuth).
- PR que toca billing (Stripe, faturas, créditos, cobrança).
- PR que adiciona/modifica webhook (entrada ou saída).
- PR que mexe em RLS (Row Level Security) ou políticas de Postgres.
- PR que rotaciona, gera ou usa secrets/credenciais.
- PR em código multi-tenant (qualquer query que filtra por org/tenant/user).
- PR que altera permissões IAM, escopos OAuth, ACL.
- PR de migration que toca tabelas sensíveis (users, sessions, payments, audit_logs).

## Pré-condição — verificar antes de iniciar

- [ ] Diff completo da PR carregado.
- [ ] Sei qual subsistema é tocado (auth | billing | RLS | webhook | secret | multi-tenant).
- [ ] Acesso a docs internas se houver (runbook security, threat model).
- [ ] Estou em ambiente seguro (não executando comandos de prod durante review).

## Workflow

### Passo 1: Listar a superfície de ataque
Para cada arquivo no diff, perguntar: "Se um atacante controlasse esta entrada, o que faria?"

### Passo 2: Verificar secrets não expostos
- [ ] Sem secret hardcoded em código (`sk-...`, `ghp_...`, `password=...`)
- [ ] Sem secret em log (string interpolada, error message, traceback)
- [ ] Sem secret em fixture de teste committed
- [ ] Sem secret em URL como query param (vai para log do proxy)
- [ ] Sem secret em commit message

### Passo 3: Verificar RLS / tenant isolation
- [ ] Toda query inclui filtro de tenant/org/user
- [ ] Sem `SELECT * FROM table` sem WHERE tenant_id (a menos que admin endpoint)
- [ ] Política RLS no Postgres não foi enfraquecida (`FOR ALL USING (true)` é red flag)
- [ ] JOIN entre tabelas preserva tenant isolation

### Passo 4: Verificar webhooks
- [ ] **Assinatura validada** antes de processar payload (HMAC, JWT signature)
- [ ] **Idempotência**: handler aceita re-entrega do mesmo evento sem efeito colateral duplicado
- [ ] **Retry behavior**: timeout, max retries, dead letter queue
- [ ] **Rate limiting**: webhook endpoint protegido contra flood
- [ ] **Logging**: payload mascarado se contém PII

### Passo 5: Verificar auth
- [ ] Sem regressão em auth gate (endpoint protegido continua protegido)
- [ ] Sem bypass criado por nova feature (ex: query param `?admin=true`)
- [ ] Session/JWT validation continua presente
- [ ] Permissões: feature requer permissão correta antes de executar

### Passo 6: Verificar billing
- [ ] Cobrança nunca executada sem confirmação (idempotência crítica)
- [ ] Refund/cancelamento requer autorização explícita
- [ ] Sem race condition em consumo de créditos (lock pessimista ou check-and-set)
- [ ] Audit log de cada transação

### Passo 7: Verificar audit logs
- [ ] Toda ação sensível gera log persistente
- [ ] Logs imutáveis (sem UPDATE/DELETE em audit table)
- [ ] Logs incluem: user, ação, timestamp, IP/origem, payload mascarado

### Passo 8: Verificar input validation
- [ ] Toda entrada de usuário validada (tipo, tamanho, formato)
- [ ] SQL injection: queries usam prepared statements / ORM com bind
- [ ] XSS: output em HTML escapado
- [ ] Path traversal: paths normalizados, sem `../`
- [ ] Command injection: sem `shell=True` com input de usuário

### Passo 9: Verificar cryptographic primitives
- [ ] Hash de senha usa algoritmo moderno (bcrypt, argon2, scrypt) — NÃO MD5/SHA1/plain
- [ ] Random para tokens usa CSPRNG (`secrets`, `crypto.randomBytes`) — NÃO `Math.random()`
- [ ] Comparação de hash usa timing-safe (`secrets.compare_digest`, `crypto.timingSafeEqual`)

### Passo 10: Verificar dependencies (se PR atualizou deps)
- [ ] `npm audit` / `pip-audit` / `bundler-audit` sem CVE crítica não-mitigada
- [ ] Versão pinned (não `^x.y.z` para libs sensíveis)
- [ ] Sem dep de mantenedor único / projeto abandonado

### Passo 11: Classificar findings — TODA finding é BLOCKER até resolver
- BLOCKER (default): bloqueia merge — implementar fix
- ACCEPTED (raro): Rodrigo aceitou risco explicitamente com justificativa documentada
- Soft pass PROIBIDO.

### Passo 12: Bloquear PR
Se qualquer finding: REQUEST CHANGES + comentar inline + escalar ao Rodrigo se necessário.

## Checklist de validação — hard-assert (zero soft pass)

- [ ] Diff lido linha a linha
- [ ] Superfície de ataque identificada
- [ ] Secrets: zero hardcoded, zero log, zero fixture, zero URL, zero commit
- [ ] RLS/tenant isolation preservada em todas as queries do diff
- [ ] Webhooks: signature + idempotência + retry + rate limit
- [ ] Auth: sem regressão, sem bypass
- [ ] Billing: idempotente, com audit
- [ ] Audit logs: toda ação sensível logada
- [ ] Input validation: SQL/XSS/path/command injection
- [ ] Crypto: senha + token + comparação timing-safe
- [ ] Deps: sem CVE crítica
- [ ] Findings classificados como BLOCKER (default)

## Output esperado

Ao final desta skill deve existir:
- Comentários inline em cada finding
- Review status: REQUEST CHANGES (se ≥1 finding) ou Approve (se zero)
- Se finding crítico: escalação ao Rodrigo registrada
- Se ACCEPTED: justificativa documentada em PR + MEMORY_DECISIONS.md

## Escalação — quando parar e pedir aprovação

- **Sempre escalar ao Rodrigo** se:
  - Finding de breach (acesso indevido detectado)
  - Secret exposto em prod (parar e rotacionar imediatamente)
  - Vulnerabilidade explorável já em produção (não esperar fix — escalar)
  - Findings em código de billing ou dados de cliente
- Se PR autor pediu para soft-passar: NÃO. Toda finding é BLOCKER salvo aceite documentado.

## Risco e rollback

Risco: Alto (review fraca permite vulnerabilidade em produção).
Rollback: se PR com finding crítico foi merged: `git revert` imediato + escalação.

## Exemplos

### Exemplo 1: PR adiciona retry em webhook
- Diff: retry com backoff no handler.
- Finding A (BLOCKER): handler removeu `verify_signature()` antes do retry.
- Finding B (BLOCKER): retry sem idempotência — webhook duplicado cobra cliente 2x.
- Ação: REQUEST CHANGES, escalar ao Rodrigo.

### Exemplo 2: PR de feature toca apenas frontend
- Diff: React components, sem backend.
- Workflow: ainda assim verificar — XSS, secrets em build, deps.
- Finding (BLOCKER): chave da Stripe publishable hardcoded em `config.ts` em vez de env var.
- Ação: REQUEST CHANGES.

### Exemplo 3: Migration aparentemente inocente
- Diff: `ALTER TABLE users ADD COLUMN avatar_url TEXT`.
- Finding (BLOCKER): sem migration de RLS — nova coluna não está coberta pela política existente.
- Ação: REQUEST CHANGES + adicionar policy update.
