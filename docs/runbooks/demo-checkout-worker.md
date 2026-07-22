# Runbook — worker de demo checkout (Refs #128)

## O que é

`apps/api/src/appsell_api/demo_checkout_worker.py` drena a fila
`app_private.claim_demo_checkout_jobs` para aplicar entitlements do checkout de
demonstração (cupom 100%, Refs #71). É um caminho **de demo**, restrito a
`local`/`CI`.

## Sintoma que originou este runbook

Em produção (`sell-autonomia` / serviço `api`) a env `APPSELL_API_ENVIRONMENT`
está com valor `ci` (débito da #119). Com isso o worker roda em produção e, se a
fila falhar, repete a cada 0.25s: ~231 warnings `demo_checkout_worker_retry` por
minuto, ocupando as linhas de log e queimando CPU de uma VPS compartilhada com
Manu, Lili e n8n.

## Diagnóstico

Desde a #128 cada retry carrega o motivo:

```
demo_checkout_worker_retry error_type=... error=... backoff_seconds=... consecutive_failures=...
```

Leia `error_type`/`error` antes de agir — o backoff só reduz a frequência, não
corrige a causa. Causas prováveis: função SQL ausente no banco alvo, RLS negando
o envelope do worker, ou credencial interna divergente.

O campo `error` é **allowlist**, não redação por regex — nenhum padrão reconhece
todo formato de segredo possível:

- `DBAPIError` → apenas classe do driver + SQLSTATE. O statement e os parâmetros
  vinculados nunca vão para o log, porque podem carregar dado de cliente.
- `KeyError` → o nome do campo, e só quando é um campo conhecido do job.
- Mensagens produzidas pelo próprio worker → registradas na íntegra.
- Qualquer outra mensagem → suprimida (`suppressed; message may contain
  sensitive data`). O `error_type` continua no log.

Se o `error` vier suprimido e você precisar do detalhe, reproduza em `local`/CI
— não afrouxe a allowlist para diagnosticar produção.

## Desligar o worker sem promover o environment

`APPSELL_API_DEMO_CHECKOUT_WORKER_ENABLED=false` desliga o worker mantendo
`APPSELL_API_ENVIRONMENT` como está.

A flag **só desliga**. Com `true` (default) o worker segue restrito a
`local`/`CI` — não há como habilitá-lo em `staging`/`production` por env.

### Procedimento em produção (requer 🟢 do Rodrigo)

1. **Backup do env completo** antes de qualquer PATCH:
   `inspectService` → salvar `.json.env` em
   `/tmp/appsell_api_env_<timestamp>.json`.
2. `services.app.updateEnv` **com o env completo**, adicionando a linha
   `APPSELL_API_DEMO_CHECKOUT_WORKER_ENABLED=false`.
   **Nunca omitir o campo `env`** — omitir apaga todas as variáveis (lição M11b).
3. Validar:
   - contagem de variáveis igual à do backup + 1;
   - `/api/health/live` e `/api/health/ready` em 200;
   - `demo_checkout_worker_retry` some das linhas de log recentes.
4. Se a contagem cair, o serviço não ficar healthy, ou o breaker abrir:
   **abortar e restaurar** o JSON do passo 1 via `updateEnv`.

### Rollback

Restaurar o env do backup do passo 1 (ou remover apenas a linha adicionada) e
redeploy. ETA: ~2 min. O worker volta ao estado anterior — incluindo o loop, que
é ruidoso mas não afeta o fluxo de compra real.

## O que este runbook NÃO resolve

Promover `APPSELL_API_ENVIRONMENT` de `ci` para `production` continua sendo a
issue #119 e **não é um flip de variável**: `config.py::validate_fail_closed_environment`
exige S3 real, ClamAV real e endpoints HTTPS remotos fora de `local`/`CI`, e o
serviço não sobe sem eles.
