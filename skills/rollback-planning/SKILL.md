---
name: rollback-planning
description: Criar plano de rollback executável antes de deploy, migration ou alteração de env em produção.
risk: Alto
platforms: claude-code, codex, cursor
---

# Rollback Planning

## Quick path (uso rápido — apenas se já existe template aprovado)

1. Identificar o que muda (deploy, migration, env, infra).
2. Backup do estado atual em `/tmp/<service>_backup_<timestamp>`.
3. Escrever comando exato de rollback.
4. Definir trigger de abort (healthz fail, error rate spike).
5. Documentar em PR antes de aplicar.

## Trigger — quando usar

- Antes de qualquer deploy em staging ou produção.
- Antes de migration de banco (mesmo "simples").
- Antes de alteração de env var em produção.
- Antes de mudança de infra (Docker, Easypanel, Traefik, DNS).
- Antes de rotação de secret/credencial.

## Pré-condição — verificar antes de iniciar

- [ ] PR ou Issue aberta para a mudança planejada.
- [ ] Sei qual serviço/recurso é afetado.
- [ ] Identifiquei ambiente (staging | produção).
- [ ] Tenho permissão para gerar backup (DB user com SELECT; env read; etc.).
- [ ] Sei onde guardar o backup (`/tmp/<service>_backup_<timestamp>`).

## Workflow

### Passo 1: Identificar o que muda (escopo exato)
- Arquivo, env var, schema, container, route, secret.
- Estado atual (`current`) vs. estado esperado (`target`).

### Passo 2: Backup obrigatório do estado atual ANTES de qualquer PATCH
- Env: `/tmp/<service>_env_backup_<YYYYMMDD-HHMMSS>.json` (mascarar secrets no echo)
- DB: `pg_dump` ou snapshot via tool de backup
- Config Easypanel/Traefik: salvar JSON via API
- Sem backup → não prossegue. Sem exceção.

### Passo 3: Escrever comando exato de rollback (não pseudocódigo)
- Deploy: `docker pull <image>:<previous-tag> && docker tag ... && restart`
- Env: `updateEnv <service> --env="$(cat /tmp/<service>_env_backup_...)"`
- Migration: `psql -f down_migration.sql` ou comando reverso explícito
- DNS: comando para restaurar registro anterior
Sempre comando exato com argumentos, paths e timestamps.

### Passo 4: Definir triggers de abort
Lista hard-assert de condições que disparam rollback automático sem nova aprovação:
- healthz retorna != 200 por > 60s
- error rate > 5% por > 2min
- env var count cai abaixo do esperado (lição M11b)
- breaker open / circuit open

### Passo 5: Estimar ETA do rollback
Quanto tempo do trigger ao serviço estável?
- Deploy revert: ~30s-2min
- Migration revert: depende de tamanho — estimar
- DNS revert: até TTL expirar (importante!)

### Passo 6: Validar rollback é executável
Se possível, rodar dry-run em staging. Sem dry-run → registrar incerteza.

### Passo 7: Documentar tudo na PR
Seção `## Rollback plan` com:
- Backup criado em (path)
- Comando exato de rollback
- Triggers de abort
- ETA esperado
- Quem executa (humano ou automático)

### Passo 8: Aprovação explícita do Rodrigo para a mudança
Plano de rollback é pré-condição, não substitui aprovação.

### Passo 9: Executar a mudança
Com plano documentado e aprovado. Monitorar healthz e métricas durante.

### Passo 10: Se trigger disparar — executar rollback IMEDIATAMENTE
Sem nova aprovação (plano já está pré-aprovado). Alertar Rodrigo via STATUS_LOG.md ou similar.

### Passo 11: Post-mortem
Atualizar MEMORY_INCIDENTS.md com timeline + root cause + se houve aprendizado generalizável.

## Checklist de validação — hard-assert

- [ ] Backup do estado atual existe em path acessível
- [ ] Comando de rollback escrito completo (sem `<...>` ou TBD)
- [ ] Triggers de abort listados com threshold concreto
- [ ] ETA estimado
- [ ] Rollback testado em staging quando possível
- [ ] Rodrigo aprovou a mudança (não apenas o plano)
- [ ] PR/Issue tem seção `## Rollback plan` preenchida

## Output esperado

Ao final desta skill deve existir:
- Arquivo de backup em path documentado
- Seção `## Rollback plan` na PR ou Issue com comando exato
- Triggers + ETA documentados
- Aprovação explícita do Rodrigo registrada

## Escalação — quando parar e pedir aprovação

- Rollback NÃO é possível (migration irreversível, dado deletado, secret rotacionado sem armazenamento prévio): **parar, não fazer a mudança**.
- Rollback requer downtime > 5min: escalar antes de aplicar.
- Mudança afeta múltiplos projetos (Manu + Lili + Hub2You): escalar — cross-project safety (lição 2026-05-22).

## Risco e rollback

Risco: Alto (mudanças em produção sem rollback = incidente).
Rollback do próprio plano: cancelar a mudança antes de aplicar; backup ainda é útil.

## Exemplos

### Exemplo 1: Deploy de Manu nova versão
- Estado atual: imagem `manu:v1.3.0` rodando.
- Backup: tag `manu:v1.3.0` mantida no registry; env var snapshot em `/tmp/manu_env_20260525-1400.json`.
- Rollback: `docker tag manu:v1.3.0 manu:rollback && updateService manu --image=manu:rollback`.
- Trigger: healthz != 200 por 60s OU error rate > 5% por 2min.
- ETA: 45s do trigger ao stable.

### Exemplo 2: ALTER TABLE em produção
- Estado atual: schema sem coluna `archived_at`.
- Backup: `pg_dump --schema-only > /tmp/db_schema_20260525-1400.sql`.
- Rollback: `ALTER TABLE conversations DROP COLUMN archived_at`.
- Trigger: query latency p95 > 500ms por 1min OU lock holder > 30s.
- ETA: 5s para drop (coluna recém-criada).
- Aprovação Rodrigo: necessária (produção).

### Exemplo 3: Mudança em env var de Lili
- Estado atual: 47 vars no service lili.
- Backup: `/tmp/lili_env_20260525-1400.json` (47 vars).
- Rollback: `updateEnv lili --env="$(cat /tmp/lili_env_20260525-1400.json)"` — NUNCA omitir campo env (lição M11b).
- Trigger: var count cai abaixo de 47 OU healthz != 200.
- ETA: 10s para revert.
