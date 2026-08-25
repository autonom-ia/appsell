# Runbook — Connect + Financial hosted checkout (#143, peças 1–5)

A venda do curso passa a liquidar **na conta do produtor**, e a plataforma retém
apenas a comissão (`application_fee_amount`). No V1 operacional, o AppSell
continua dono de produto, oferta, preço, cupom e fulfillment; o
autonomia-financial cria o Hosted Stripe Checkout, registra fatura/pagamento e
assina o webhook de liberação de acesso de volta para o AppSell.

O provider `local_stripe` continua no código como fallback compatível. Em PRD,
o canal novo deve usar `APPSELL_CHECKOUT_PROVIDER=financial_checkout`.

Nenhum valor de secret aparece neste documento. Só nomes de variável.

## O que a cadeia liga

```
produtor  → /payouts  →  POST /api/sellers/<id>/payout-account
                      →  accounts.create (controller travado, ver abaixo)
                      →  bind em seller_payout_account   (audit payout_account.bound)
                      →  Account Session  →  componente embarcado na NOSSA página
Stripe    → POST /api/webhooks/stripe/connect  (account.updated)
                      →  refresh do cache de status  (audit payout_account.status_changed)
comprador → POST /api/checkout/stripe/create-session
                      →  AppSell monta snapshot server-side do produto/oferta/preço/cupom
                      →  POST /financial/internal/external-checkout/checkout-intents
Financial → tela publica /financial/public/external-checkout/:orderId
                      →  Hosted Stripe Checkout NA CONTA CONECTADA
                         + application_fee_amount calculado pelo AppSell
Stripe    → POST /financial/webhooks/stripe  (checkout.session.completed)
                      →  Financial valida gateway/valor/conta
                      →  Financial registra invoice/payment one_time sem assinatura
                      →  webhook assinado → AppSell ingress → entitlement
```

## Config de conta conectada (verificada empiricamente — não alterar sem novo probe)

```
country: "BR"
controller.requirement_collection = "stripe"     # a Stripe mantém o KYC (PF e PJ)
controller.stripe_dashboard.type  = "none"       # o produtor nunca vê painel do gateway
controller.losses.payments        = "stripe"     # negativo irrecuperável não sobra pra nós
controller.fees.payer             = "account"    # o produtor paga as taxas do gateway
capabilities: card_payments + transfers          # obrigatório com dashboard "none"
```

Duas regras da API que a documentação não diz e que travam o desenho:
`requirement_collection:"application"` (KYC na nossa UI) **força**
`losses:application` + `fees:application` + `dashboard:none` — ou seja, UI própria
de KYC e "Stripe absorve prejuízo" são incompatíveis. E
`stripe_dashboard.type:"express"` **força** a plataforma a pagar taxas e assumir
liability. Por isso `dashboard:"none"` + `requirement_collection:"stripe"`.

Chargeback: em direct charges o produtor é merchant of record, então o estorno
debita o saldo dele primeiro. `losses.payments` só decide quem cobre se ele não
tiver saldo — e escolhemos `stripe`.

## Env vars novas

| Serviço | Var | Formato | Papel |
|---------|-----|---------|-------|
| `web` | `APPSELL_STRIPE_PUBLISHABLE_KEY` | `pk_live_…` | o servidor a entrega ao browser na resposta de `/api/sellers/<id>/payout-account`. **Pública por design**, mas **sem** o prefixo `NEXT_PUBLIC_`: esse prefixo embute no bundle em tempo de build, e a imagem é construída sem a var. O modo precisa casar com a chave secreta. |
| `web` | `APPSELL_STRIPE_CONNECT_WEBHOOK_SECRET` | `whsec_…` | verifica a assinatura do endpoint Connect. **Tem de ser diferente** de `APPSELL_STRIPE_WEBHOOK_SECRET` — o código recusa reuso (`stripe_webhook_secret_reused`). |

As já existentes (`APPSELL_STRIPE_SECRET_KEY`, `APPSELL_STRIPE_WEBHOOK_SECRET`,
`APPSELL_CHECKOUT_WEBHOOK_ENDPOINT_KEY`, `APPSELL_CHECKOUT_WEBHOOK_SECRET_B64`,
`APPSELL_CHECKOUT_PRODUCT_MAP_JSON`) continuam valendo.

Para o provider Financial hosted, o `web` também exige:

| Serviço | Var | Formato | Papel |
|---------|-----|---------|-------|
| `web` | `APPSELL_CHECKOUT_PROVIDER` | `financial_checkout` | ativa o caminho Financial sem mudar a rota pública do AppSell. |
| `web` | `APPSELL_FINANCIAL_OPERATOR_ID` | UUID | operador/empresa no Financial que recebe a fatura. |
| `web` | `FINANCIAL_API_URL` | HTTPS | base interna do Financial. |
| `web` | `FINANCIAL_INTERNAL_API_KEY` | secret | chave server-to-server para `/financial/internal/external-checkout/checkout-intents`. |

No Financial PRD, o deploy gate exige:

| Serviço | Var/SSM | Formato | Papel |
|---------|---------|---------|-------|
| `financial` | `/autonomia/prod/financial/public-base-url` | HTTPS | monta a URL publica da tela hospedada. |
| `financial` | `/autonomia/prod/financial/appsell-webhook-secret-b64` | SecureString base64 32 bytes | assina o fulfillment enviado ao AppSell. |

## Ordem de deploy (cada passo é gate humano)

1. **Backup antes de qualquer PATCH**: env completo dos serviços `web` e `api`
   (o campo `env` do Easypanel precisa ir COMPLETO — omitir apaga todas as vars)
   e `pg_dump` do banco. Guardar com timestamp.
2. **Migrations 0021→0024** em `appsell_native_90`, na ordem. O usuário tem de ser
   `appsell_native_90_migrator` e a URL `postgresql://` (a env de runtime é
   `postgresql+asyncpg://`). Sem isso, RLS recusa.
   Confirmar depois: `select app_private.schema_ready()` → `true`. Se voltar
   `false`, **a API não sobe** — o gate de readiness pina o conjunto de revisões.
3. **Endpoint de webhook Connect no Stripe**: novo endpoint apontando para
   `https://sell.autonomia.site/api/webhooks/stripe/connect`, marcado para
   **eventos de contas conectadas**, assinando `account.updated`,
   `checkout.session.completed` e `checkout.session.async_payment_succeeded`.
   O `whsec` só é devolvido na criação — gravar direto na env.
4. **Env do `web`**: `+APPSELL_STRIPE_PUBLISHABLE_KEY`,
   `+APPSELL_STRIPE_CONNECT_WEBHOOK_SECRET`,
   `APPSELL_CHECKOUT_PROVIDER=financial_checkout`,
   `+APPSELL_FINANCIAL_OPERATOR_ID`. Conferir que a contagem de vars
   não caiu; se cair, abortar e restaurar o backup.
5. **Financial antes do AppSell**: publicar o Financial via PR/workflow,
   confirmar migration `042_appsell_external_checkout_invoices.sql` aplicada e
   `release-smoke` com `externalCheckoutValidated=true`.
6. **Redeploy `web` e `api`** (`services.app.deployService` — `services.app.deploy`
   dá 404). Env sozinha não muda processo em execução.
7. **Prova de deploy**: `POST /api/webhooks/stripe/connect` sem assinatura tem de
   responder **400 `missing_signature`** (e não 404).
8. **Produto do produtor real**: `external_product_mapping` via
   `python -m appsell_api.provision_checkout` (CLI idempotente, nunca INSERT na
   mão) + entrada correspondente em `APPSELL_CHECKOUT_PRODUCT_MAP_JSON`. O
   webhook de fulfillment faz o caminho inverso da chave para achar o seller: se
   a chave mapear para dois produtos, ele **recusa** em vez de adivinhar.

## Teste ao vivo (dinheiro real)

1. Entrar como o produtor, ir em **Recebimentos**, clicar em
   **"Conectar conta Autonom.ia"**, completar a verificação no componente
   embarcado (PF ou PJ). Nenhuma tela do gateway abre; o único lugar onde o nome
   dele aparece é o aceite do contrato de serviço, exigência legal.
2. Esperar o card mostrar **"Pronta para receber"** (vem do `account.updated`).
   Enquanto não estiver, o checkout responde **409** de propósito.
3. Gerar o link do produto e pagar **R$ 1,00** no cartão.
4. Conferir no Financial: `checkout_orders` provisionado, `invoices` paga com
   `user_subscription_id IS NULL`, `billing_cycle='one_time'`, snapshot do
   produto AppSell e `payments.gateway_payment_id` igual ao PaymentIntent.
5. Conferir no AppSell: o comprador recebeu entitlement do produto correto.
6. Conferir na Stripe: o pagamento aparece na conta **do produtor**; a comissão
   aparece como `application_fee` para a plataforma.

## Rollback

| Sintoma | Ação |
|---------|------|
| Checkout errado / cobrança indevida | Zerar `APPSELL_CHECKOUT_PRODUCT_MAP_JSON` para `{}` + redeploy → `create-session` passa a responder 409 `product_not_configured`. Nenhuma venda nova entra. |
| Financial checkout indisponível | Voltar `APPSELL_CHECKOUT_PROVIDER=local_stripe` somente se o mapa/produto antigo estiver preparado; caso contrário, usar o kill switch do mapa e manter webhooks no ar. |
| Webhook Financial→AppSell com problema | Manter webhook Stripe do Financial ativo, corrigir o receiver AppSell e deixar retry/reconciliação tratar. Desabilitar endpoint Stripe primeiro pode gerar pago sem fulfillment. |
| Webhook Connect com problema | Desabilitar o endpoint no Stripe somente depois de drenar sessões abertas. O fulfillment para; o pagamento não. |
| Env corrompida | Restaurar o backup do passo 1 (campo `env` COMPLETO) + redeploy. |
| Migration | `pg_restore` **não** é rollback aqui: o downgrade de 0021 recusa enquanto existir binding, porque a conta conectada é estado do mundo real — um produtor já verificou identidade contra ela. Preservar o vínculo é o comportamento correto. |
