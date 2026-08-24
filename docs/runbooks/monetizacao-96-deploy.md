# Runbook — deploy da monetização (#96): migrations 0019+0020 + web + api

> **Status:** plano histórico/futuro de monetização. Não é o registro do
> release atual de produção, que está em `0032` e no SHA
> `2c8a36897eadf624955810ffdaee7b33c6239334`. Não executar estes passos sem
> novo Issue, PR, aprovação explícita, backup e plano de rollback.
>
> Escopo: primeiro deploy do bundle de monetização em `sell.autonomia.site`
> (Easypanel, VPS compartilhada Manu/Lili/n8n — cross-project safety).
> Pré-requisitos: PRs #124, #125 e a PR do checkout mergeadas em main com CI
> verde e review codex aprovado.

## Ordem de execução

1. **Backups (obrigatório antes de qualquer PATCH)**
   - Env dos serviços web e api: salvar JSON em
     `/tmp/appsell_<serviço>_env_<timestamp>.json` (via console Easypanel).
   - Banco: dump lógico do database do Appsell antes das migrations
     (`pg_dump -Fc`) em local seguro; validar tamanho > 0.
2. **Migrations (0019_offer_status + 0020_checkout_fulfillment)**
   - Rodar o job de migrate padrão do deploy (mesmo caminho usado nos deploys
     anteriores — migrate antes de trocar imagem).
   - Validar: `select version_num from alembic_version` = `0020_checkout_fulfillment`
     e `select app_private.schema_ready()` = `t`.
3. **Envs novas (api)** — adicionar SEM omitir as existentes (updateEnv com o
   env COMPLETO; omitir = wipe, lição M11b):
   - `APPSELL_API_WEBHOOK_LOCAL_KEYRING_B64_JSON` = keyring com
     `checkout-prod-v1` (secret novo de 32 bytes, independente de todas as
     chaves internas — o resolver valida e aborta se colidir).
   - `APPSELL_API_WEBHOOK_ENV_KEYRING_ALLOWED=true`.
4. **Envs novas (web)** — mesmas regras:
   - `APPSELL_CHECKOUT_PRODUCT_MAP_JSON` (mapa `<sellerSlug>/<productSlug>` →
     `financial_product_key`).
   - `APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY` (o plaintext do endpoint key).
   - `APPSELL_CHECKOUT_WEBHOOK_SECRET_B64` (mesmo secret do keyring da api).
   - `FINANCIAL_API_URL`, `FINANCIAL_INTERNAL_API_KEY` (já definidos no
     .env.example da #112; conferir presença).
5. **Deploy das imagens** web e api (mutations Easypanel; queries GET dão 400 —
   ler env pelo console).
6. **Provisionamento do checkout no banco** (uma vez, idempotente):
   ```
   APPSELL_PROVISION_DATABASE_URL=<url do migrator> \
   APPSELL_PROVISION_ENDPOINT_KEY=<plaintext endpoint key> \
   python -m appsell_api.provision_checkout \
     --seller-slug <slug do seller> --secret-reference checkout-prod-v1 \
     --external-product-id <product key no Financial> \
     --product-slug <slug do produto> --confirm provision-checkout-v1
   ```
7. **Healthz pós-deploy (obrigatório)**
   - `GET /api/health/live` e `GET /api/health/ready` → 200 (não existe
     `/api/health`).
   - Vitrine pública responde; página do produto renderiza preço.
   - Log do api sem `fulfillment_worker_retry` em loop.

## Safety aborts (parar e reverter sem novo 🟢)

- Contagem de env vars do serviço CAIU após PATCH → restaurar backup imediato.
- Serviço não healthy em 5 min → rollback.
- `schema_ready()` = f após migrate → investigar antes de trocar imagem
  (imagem antiga continua compatível: 0019/0020 são aditivas).

## Rollback (pré-aprovado)

- **Imagens**: redeploy da imagem anterior (tag registrada no Easypanel) —
  ETA ~3 min por serviço.
- **Envs**: restaurar o JSON de backup do passo 1 via updateEnv com env
  completo — ETA ~2 min.
- **Migrations**: NÃO reverter por padrão (0019/0020 são aditivas e a imagem
  antiga ignora as tabelas novas). Downgrade da 0020 só com fila
  `fulfillment_job` vazia (a migration recusa caso contrário); downgrade da
  0019 recusa se houver ofertas não-draft — preservação de dados por design.
- **Provisionamento**: para desativar o checkout sem remover dados,
  `update webhook_endpoint set status='revoked'` no endpoint provisionado
  (reversível com `status='active'`).

## Registro

- Anotar em `docs/audit/` timestamp de cada passo, tags de imagem, e os
  healthchecks. STATUS_LOG em caso de abort.
