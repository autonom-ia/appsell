# Runbook — checkout Stripe live (#143, slice 1: platform-direct)

Cobra a conta Stripe **da própria plataforma** (Connect é fatia posterior). O
dinheiro do curso entra na Autonom.ia; repasse ao produtor não existe nesta
fatia.

Nenhum valor de secret aparece neste documento. Só nomes de variável.

## O que a fatia liga

```
browser → POST /api/checkout/stripe/create-session   (preço lido server-side)
        → Stripe Checkout (cartão, BRL, one-time)
Stripe  → POST /api/webhooks/stripe                  (assinatura verificada pelo SDK)
        → self-emit assinado (generic_hmac_v1)
        → /v1/webhooks/checkouts/:endpointKey  →  webhook_event → outbox_event
        → fulfillment_job → entitlement_grant → buyer_entitlement
```

## Env vars (serviço `web`)

| Var | Formato | Papel |
|-----|---------|-------|
| `APPSELL_STRIPE_SECRET_KEY` | `sk_live_…` / `sk_test_…` | cobra a conta da plataforma. O prefixo decide o modo — o webhook recusa evento de modo divergente. |
| `APPSELL_STRIPE_WEBHOOK_SECRET` | `whsec_…` | verifica a assinatura do endpoint Stripe. **Um por endpoint**; recriar o endpoint gera outro. |
| `APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY` | 32–64 url-safe | identifica o endpoint do ingress do domínio. |
| `APPSELL_CHECKOUT_WEBHOOK_SECRET_B64` | base64 canônico de 32 bytes | assina o self-emit. |
| `APPSELL_CHECKOUT_PRODUCT_MAP_JSON` | `{"<seller>/<product>":{"financial_product_key":"…"}}` | mapa interino produto→chave (Refs #96). |

Env var (serviço `api`): `APPSELL_API_WEBHOOK_LOCAL_KEYRING_B64_JSON` — JSON
**puro** (não base64) com lista de `{"reference","key"}`; `key` = base64 canônico
de 32 bytes. A `reference` tem de ser a mesma gravada em
`webhook_endpoint.secret_reference`.

Nomes com prefixo `APPSELL_` não são cosmética: o env de produção já foi gravado
assim e o código foi alinhado ao env, não o contrário.

## Pré-requisitos no Stripe

1. Endpoint registrado em Developers → Webhooks apontando para
   `https://sell.autonomia.site/api/webhooks/stripe`.
2. Eventos assinados: `checkout.session.completed` **e**
   `checkout.session.async_payment_succeeded`.
3. Métodos de pagamento: a rota **fixa** `payment_method_types: ["card"]`, então
   a configuração do Dashboard (`pmc_…`) não altera o comportamento. Ver
   "Adicionar Pix/boleto" abaixo antes de mexer.

## Provisionamento no banco (idempotente)

```
APPSELL_PROVISION_DATABASE_URL=postgresql://... \
APPSELL_PROVISION_ENDPOINT_KEY=<mesma da env do web> \
python -m appsell_api.provision_checkout \
  --seller-slug <slug> --secret-reference <ref> \
  --external-product-id <financial product key> --product-slug <slug> \
  --confirm provision-checkout-v1
```

Armadilhas confirmadas em produção:

- A URL tem de ser `postgresql://` — `APPSELL_API_DATABASE_URL` usa o dialeto
  SQLAlchemy `postgresql+asyncpg://` e o `psycopg` rejeita.
- Rodar com um role que herde `appsell_owner` (ex. `appsell_native_90_migrator`).
  Com o role de runtime a **RLS** filtra `seller_organization` e o erro aparece
  como `seller organization not found or inactive` mesmo com o seller ativo.

## Adicionar Pix ou boleto depois

Pix no Brasil é **invite only** na Stripe: `pix_payments` responde
`not requestable for accounts in BR` na API de capabilities, e na
`payment_method_configuration` fica `available: false`. Liberar exige pedido à
Stripe pelo Dashboard, não código.

Quando liberar, os dois passos são obrigatórios **juntos**:

1. incluir o método em `payment_method_types` em
   `app/api/checkout/stripe/create-session/route.ts`;
2. confirmar que o método está em `PAID_EVENT_TYPES` em
   `app/api/webhooks/stripe/route.ts` (já contempla
   `checkout.session.async_payment_succeeded`).

Só o passo 1 = método de notificação atrasada cobra o comprador e **nunca**
libera o curso.

## Safety aborts (parar e reverter sem novo 🟢)

- Contagem de vars do serviço cair após PATCH.
- Serviço não healthy em 5 min.
- Checkout devolve `stripe_not_configured` (503) → nome de env divergente.
- Pagamento aprovado no Stripe sem linha em `buyer_entitlement`.

## Rollback

Este release **não tem migration**. Logo, `pg_restore` **não** é rollback e
**não** é pré-aprovado — ver "Banco" abaixo.

Duas armadilhas governam a ordem dos passos:

1. **Desabilitar o webhook não invalida Checkout Session já criada.** Uma sessão
   aberta continua pagável por ~24h. Se o endpoint cair primeiro, o comprador
   paga e não existe webhook para liberar o curso.
2. **O Stripe não reenvia evento que já respondemos 2xx.** Tudo o que foi
   confirmado antes do rollback só volta por reconciliação manual, nunca por
   retry.

### Ordem obrigatória (pré-aprovada)

1. **Parar de criar cobrança nova, mantendo o webhook no ar.** Redeploy da tag
   anterior do `web` (a rota `create-session` deixa de existir) **ou** esvaziar
   `APPSELL_CHECKOUT_PRODUCT_MAP_JSON` (todo produto passa a responder
   `product_not_configured` 409). O endpoint de webhook **continua ativo**.
2. **Drenar as sessões abertas.** Dashboard → Payments → sessões `open` do
   período. Para cada uma: expirar (`POST /v1/checkout/sessions/:id/expire`) ou
   aguardar o pagamento e confirmar que gerou `buyer_entitlement`.
3. **Reconciliar o período.** Listar as sessões `complete`/`paid` da janela e
   confirmar `buyer_entitlement` para cada uma. Divergência → liberar acesso
   manualmente ou estornar; nunca deixar pago sem acesso.
4. **Só então** desabilitar o endpoint de webhook no Stripe.
5. Env: restaurar do backup do campo `env` **completo**
   (`env_backup_{web,api}_<timestamp>.txt`). Omitir `env` no `updateEnv` do
   Easypanel **apaga todas** as vars (lição M11b).

### Banco — exige 🟢 explícito, não é pré-aprovado

Restaurar o dump pré-release **destrói dados criados depois do snapshot**:
`webhook_event`, `outbox_event`, `fulfillment_job`, `entitlement_grant`,
`buyer_entitlement` — inclusive de compradores que pagaram e foram atendidos
corretamente. E como o Stripe já recebeu 2xx, esses eventos **não voltam**.

Só faz sentido com corrupção de dados comprovada, como incidente, com aprovação
explícita do Rodrigo, backup do estado **atual** antes do restore, e a lista de
pagamentos da janela em mãos para re-liberar acesso manualmente.

### Estorno

Rollback **não** estorna. Estorno é ação separada no Dashboard e decisão do
Rodrigo.

## Dívida conhecida

`APPSELL_API_ENVIRONMENT=ci` em produção (#119) é o que permite o keyring de
webhook via env. Trocar para `production` sem habilitar
`webhook_env_keyring_allowed` quebra pagamento→acesso. Ler #119 antes de mexer.

## Registro

Anotar por execução: timestamp, tag de imagem, contagem de vars antes/depois,
`endpoint_key_sha256` provisionado, e o resultado do smoke (valor cobrado,
`order_id`, `buyer_entitlement` criado).
