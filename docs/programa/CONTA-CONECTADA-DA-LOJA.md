# A conta conectada da loja: caminhos de hoje e como unificar

Refs #439, #440. Trilha 2 · Dinheiro. Só análise de código; nada em produção.

Sell em `origin/main`; Financial em `autonomia-financial` `origin/main` 7d68ed1,
lido sem alterar nada.

## O que a Orquestração verificou na Stripe (só leitura, 24/10/2026)

- Os dois operadores live do Financial usam **a mesma chave**, a da conta de
  plataforma da Autonom.ia.
- Operador `13a30647`: as cobranças caem na **conta conectada "Hub2You LTDA"**
  (express; cobranças e repasses ativos). É cobrança direta via `Stripe-Account`,
  **sem application fee**.
- Operador `bf9daec0`: cobra na própria conta da Autonom.ia.
- A conta conectada "AUTONOM.IA" existe, mas não cobra.
- Em 60 dias: 1 cobrança na plataforma e 24 na conta da Hub2You, quase todas do
  faturamento do Chat2You (metadata `chatwootAccountId`/`invoiceId`), não do
  Sell.

Conclusão: o caminho "conta conectada da loja" já funciona na Stripe, mas foi
ligado **direto no Financial**, no `account_id` do gateway do operador. Não passa
pelo cadastro de recebimentos do Sell.

---

## (a) O cadastro de recebimentos do Sell, ponta a ponta

**É código vivo e alcançável em produção, mas o checkout de produção não o usa.**

| Passo | Onde | O que faz |
|---|---|---|
| Tela | `app/(protected)/settings/payouts/page.tsx:20` → `app/components/payout-connect-card.tsx` | Acha a loja de que a pessoa é dona (`:123-135`) e lê o estado (`:98`). O botão abre o onboarding embutido da Stripe (`:176-211`, `ConnectAccountOnboarding` em `:289-291`) |
| BFF | `app/api/sellers/[sellerId]/payout-account/route.ts:77-162` | Na primeira vez, cria a conta (`:121`), vincula (`:124-131`) e abre a sessão de onboarding (`:148`). Se o vínculo falha, apaga a conta criada (`:136`) |
| Stripe | `lib/stripe-connect.server.ts:26-39` | `accounts.create` com a **chave da plataforma** (`APPSELL_STRIPE_SECRET_KEY`), `country: "BR"`, `controller` com coleta pela Stripe, sem painel da Stripe para o vendedor, perdas com a Stripe e taxas pagas pela conta (`fees.payer: "account"`), e capabilities `card_payments` e `transfers` |
| Gravação | `apps/api/migrations/versions/0021_seller_payout_account.py:113-135`; `payout_routes.py:160-177` | `seller_payout_account` guarda `provider_account_id`, `charges_enabled`, `payouts_enabled` e `details_submitted`. Só o `seller_owner` vincula. Trocar a conta de uma loja já vinculada é recusado e auditado (`0022:84`) |
| Estado | `app/api/webhooks/stripe/connect/route.ts:124-183` → `0023:86-144` | O `account.updated` do endpoint Connect do Sell atualiza os flags. Não existe polling |

Quem lê a conta vinculada:

| Leitor | Onde | No checkout de produção (`financial_checkout`)? |
|---|---|---|
| Card e aviso do painel | `payout-connect-card.tsx:98`, `dashboard-overview.tsx:244-282` | Só tela. O aviso do painel pede conta conectada mesmo no modo Financial, onde ela não é usada: é uma inconsistência |
| Editor de produto | `product-editor-workspace.tsx:587,857` | Pula a checagem no modo Financial |
| Trava de publicação paga (P4223) | `0034:207-213` | Contornada: no modo Financial o editor publica como gratuito e ativa a oferta depois (`product-editor-workspace.tsx:166-175`) |
| `resolve_payout_destination` | `0024:71`, `payout_sync_routes.py:106-143` | **Não**: `create-session/route.ts:105-117` só a chama fora do modo Financial |
| Checkout `local_stripe` | `create-session/route.ts:110-116,218-226` | Não é o de produção. Cobra direto na conta da loja, com `application_fee_amount` |
| Webhook Connect do Sell (venda paga) | `connect/route.ts:240-251` | Só recebe venda paga criada pelo `local_stripe` |

**Veredito**: o onboarding está vivo, mas o que ele grava só alimenta telas e o
checkout legado. O checkout de produção manda `connect.accountId: null`
(`create-session/route.ts:153`). Ninguém usa a conta conectada que a loja
cadastrou.

## (b) Como o `account_id` do operador foi parar no gateway

- O gateway é cadastrado **à mão, por admin**:
  - Rotas `POST`/`PATCH /financial/company-gateways` (`src/routes.ts:2094`,
    `:2109`) exigem um JWT com `financial.admin` (`requireAdmin`,
    `routes.ts:4966-4969`).
  - O corpo inclui `accountId` (`companyGatewaySchema`, `routes.ts:288-299`).
  - Não existe rota por chave interna (`x-api-key`) para operador ou gateway.
- `createCompanyGateway` (`financial-service.ts:782-826`) **não confere o
  `accountId`** ao gravar.
- Existe uma validação separada e opcional (`/validate`,
  `gateway-client.ts:200-229`):
  - com chave de plataforma e `accountId` diferente da dona da chave, chama
    `GET /accounts/{accountId}`; um 200 prova que a conta está conectada à
    plataforma;
  - ela devolve `charges_enabled`, mas não reprova quando ele é falso.
- **Um gateway ativo por operador e modo**: o índice único parcial
  `uq_financial_active_company_gateway_mode` (`020_bind_one_time_credit_gateway.sql:82-87`)
  substituiu o `UNIQUE` da 002. Por isso o erro "mais de um gateway Stripe
  ativo" de `resolveActiveStripeGateway` (`financial-service.ts:4670-4671`) não
  chega a acontecer.
- Depois que um pedido de crédito avulso usa o gateway, `account_id`, `mode` e
  `operator_id` **não mudam mais**. O trigger
  `guard_bound_company_gateway_mutation` (`020:91-118`) obriga a criar outro
  gateway.
- **Operador** vem da sincronização do Admin, por fila
  (`src/financial-sync-consumer.ts:69-80`), ou da rota admin
  (`routes.ts:1660-1700`). Operador sincronizado não pode ser editado
  (`financial-service.ts:262-270`). Não existe API para criar operador ou
  gateway **por loja**.

Quem depende do `account_id` do gateway do operador, ou seja, o que não pode
quebrar (`financial-service.ts`):

| Fluxo | Onde |
|---|---|
| Checkout externo (Sell) | `:3362`, `:3455` (`connectedAccountId ?? accountId`, `gateway-client.ts:386`) |
| Checkout com PaymentIntent e SetupIntent | `:2768`, `:2802` |
| Pagamento de fatura | `:3118`, `:3160`, `:3261` |
| Renovação de assinatura (off-session) | `:6481`, `:6629` |
| Cartão salvo | `:6731`, `:6784`, `:7063` |
| Crédito avulso | `:2480`, `:2514-2567`, `:5165-5195`, webhook `:8187` |
| Clientes e meios de pagamento | `payment_gateway_customers` por `company_gateway_id` (migration 021). Vivem na conta para a qual o gateway aponta |
| Webhook | `stripeEventMatchesGateway` (`:8249-8253`), `resolveVerifiedStripeWebhookGateway` (`:4618-4660`) |
| Smoke do release | `:953-960` |

O faturamento do Chat2You na conta da Hub2You (metadata `chatwootAccountId`)
**não sai do Financial**: o repositório não tem `chatwoot`. Outro sistema cria
essas cobranças, provavelmente com a mesma chave de plataforma. Isso é inferência
e precisa ser confirmado no repositório do Chat2You. **Mudar o gateway do
operador `13a30647` pode afetar esse faturamento, as renovações e os clientes
salvos.**

### Webhook da conta conectada

- Existe um só endpoint, `POST /financial/webhooks/stripe` (`routes.ts:2663`).
  Ele tenta o segredo de cada gateway (`financial-service.ts:3644-3668`).
- O evento casa quando o segredo confere e `event.account === gateway.accountId`
  (`:8249-8253`).
- Para o operador `13a30647`, os eventos da conta da Hub2You **casam**, desde que
  o segredo de webhook desse gateway seja o de um endpoint **Connect** da Stripe.
  O repositório não mostra esse endpoint; ele está só na configuração da Stripe.
- Para um pedido com `connect.accountId` diferente do `account_id` do gateway (o
  caso de várias lojas sob um mesmo operador), o evento **não casa**: "Webhook não
  verificado para nenhum gateway configurado." (`:3666`).

## (c) Proposta para unificar

Princípio: **a loja cadastra a própria conta pelo Sell, como já faz, e o Sell
manda essa conta e a comissão em cada pedido.** O Financial já aceita isso no
checkout externo (`selection.connect`, `routes.ts:478-481`; comissão em
`gateway-client.ts:381-383`). Falta o retorno: o webhook da conta conectada.

Operador: as lojas do autosserviço ficam sob um **operador compartilhado** da
plataforma, cujo gateway não tem `account_id`. A conta de cada loja vai por
pedido, nunca no gateway. A Hub2You continua no próprio operador (ver d).

| # | Mudança | Onde | Nova ou altera o que existe | Quem depende do que existe |
|---|---|---|---|---|
| S1 | No modo `financial_checkout`, resolver a conta da loja (`resolve_payout_destination`, que já existe), exigir `charges_enabled` e enviar `connect.accountId` e `applicationFeeAmountMinor` | Sell, `create-session/route.ts` | **Altera** código do Sell, **atrás de flag por loja** (`APPSELL_FINANCIAL_CONNECT_SELLER_IDS`, vazia por padrão) | Toda venda do Sell. Fora da flag, o pedido sai igual ao de hoje (`null`), byte a byte |
| S2 | Comissão menor que o líquido: o Sell hoje limita ao valor inteiro (`lib/checkout-fee.ts:23-26`) e o Financial recusa ≥ (`financial-service.ts:3511-3514`). Preço mínimo ao publicar e teste do caso-limite | Sell | Altera o Sell | Só o caminho com flag |
| S3 | O aviso do painel sobre conta conectada passa a seguir o modo real do checkout | Sell, `dashboard-overview.tsx:244-282` | Altera o Sell (texto) | Nenhum |
| F1 | **Receptor novo de eventos Connect**: rota própria (ex.: `POST /financial/webhooks/stripe-connect`), segredo próprio do endpoint Connect da plataforma, e casamento pelo pedido. O pedido grava `selection.connect.accountId`, e o evento só vale se `event.account` for essa conta **e** o pedido for daquela sessão. Para a liquidação, chama o mesmo caminho do checkout externo | Financial | **Nova e isolada**. Não toca em `stripeEventMatchesGateway`, `resolveActiveStripeGateway` nem `company_gateways` | Eventos cujo `event.account` é o `account_id` de algum gateway (a Hub2You) **são ignorados** pelo receptor novo e seguem no endpoint de hoje. Assim nada muda para o operador `13a30647`. A idempotência vem do estado do pedido |
| F2 | Estorno e disputa de pedido conectado viram aviso ao Sell (com `seller_id`), para revogar o acesso | Financial | **Nova** (#151, financial#150) | Gate do programa: nada de divisão de pagamento em produção antes disso |
| F3 | Endpoint Connect na Stripe para a conta de plataforma, apontando para F1, e o segredo dele no SSM | Stripe e infra | Configuração nova | **Não pode reaproveitar** o segredo do gateway `13a30647`, se esse já for um endpoint Connect: seriam dois receptores do mesmo evento. Confirmar no painel antes |

**O que não muda em hipótese nenhuma**: o gateway e o `account_id` do operador
`13a30647`, o `account_id` nulo do `bf9daec0`, e as funções de casamento e de
escolha de gateway. Os dois tipos de conta convivem (express na Hub2You, sem
painel nas lojas do Sell), porque nos dois casos é cobrança direta.

Ordem: F1 e F3 → S1 e S2 com uma loja de teste na flag → F2 antes de qualquer
loja real → conciliação (#147/#459) → piloto com OK do Rodrigo. Isso é a #440.

## (d) A Hub2You no Sell, quando a Plataforma trouxer o operador da loja

- O Sell manda `operatorId: seller.financial_operator_id`
  (`create-session/route.ts:125`). Com a loja Hub2You vinculada ao operador
  `13a30647`, a venda do Sell cai **na conta conectada "Hub2You LTDA"**, pelo
  `account_id` do gateway, sem comissão. É o mesmo caminho do faturamento do
  Chat2You. **A Hub2You já é a vendedora de fato.**
- O webhook dessa venda casa no endpoint de hoje (`event.account ===
  gateway.accountId`), desde que o segredo do gateway seja o do endpoint Connect.
  O `purchase.approved` chega ao Sell como hoje.
- **Recomendação: a Hub2You fica exatamente assim.** Não entra na flag do S1, e
  nada muda no gateway dela.
- Se um dia houver comissão sobre a venda da Hub2You no Sell, o caminho só do
  Sell é mandar `connect.accountId` igual à mesma conta, mais a comissão. O
  Financial usa `connectedAccountId ?? accountId`, então a conta é a mesma e o
  webhook continua casando. Para isso a loja precisa da conta em
  `seller_payout_account`. Como a conta foi criada fora do Sell, isso exige um
  vínculo operacional (a função de vínculo existe, `0021:181`), com OK do
  Rodrigo. Não é para agora.
- A divergência real com a decisão da #439 está **no operador `bf9daec0`**:
  qualquer loja vinculada a ele vende na conta da Autonom.ia. Hoje nenhuma loja do
  Sell deveria estar nele. A flag do #469 fica vazia até F1 e S1 existirem.

## (e) A lacuna de estorno

Conciliação Stripe × Sell feita pela Orquestração (#439, comentário 6383192898):

- O Financial grava na sessão Stripe `metadata.orderId`, e o SHA-256 dele bate
  com `external_purchase_id_sha256` nas 4 vendas do Sell.
- A 5ª venda paga (`…avqK`) não chegou ao Sell e foi estornada.
- A `…ybva` foi estornada, mas **o acesso continua `completed` no Sell**.

**Resposta: o Sell sabe revogar, mas o Financial nunca avisa o estorno de uma
venda do checkout externo.**

### Sell: pronto para revogar

- A entrada aceita `purchase.refunded` (`apps/api/src/appsell_api/webhook_adapters.py:14`;
  enum do evento em `0012_checkout_webhook_ingress.py:94` e
  `0020_checkout_fulfillment.py:102`).
- O fulfillment trata o estorno (`0020_checkout_fulfillment.py:654-700`):
  - marca o grant da compra como `refunded`, mesmo que o estorno chegue antes da
    aprovação;
  - revoga a `buyer_entitlement` (`status='revoked'`) quando nenhum grant ativo
    resta para o par comprador × produto;
  - audita `commerce.fulfillment_entitlement_revoked`.
- O estorno vence a aprovação fora de ordem: uma aprovação que chega depois não
  concede acesso (`0020:600-622`).
- As reescritas posteriores de `apply_checkout_fulfillment` (0049, 0051) inserem
  trechos por âncora e mantêm esse ramo.
- Testes: `tests/test_postgres_checkout_fulfillment.py:393,420,520`.

### Financial: não emite

- O checkout externo emite **só** `event_type: "purchase.approved"`
  (`src/financial-service.ts:4518`, em `deliverExternalCheckoutFulfillment`,
  `:4509-4560`). Não existe outro `event_type` nesse caminho.
- Um evento de estorno (`charge.refunded`, `refund.*`) não é de
  `checkout.session`. Por isso não acha o pedido do checkout externo
  (`:4170-4173`) e não entra no ramo que entrega ao Sell (`:4174-4178`).
- Ele segue para o ledger (`stripeSettlementRecorder.record`, `:3698-3703`) e,
  só se for de crédito avulso, para o mapeamento de crédito avulso
  (`:8168-8176`).
- O `purchase.refunded` que existe no Financial é do **crédito avulso**, outro
  produto: `src/one-time-credits.ts:158,175-180`, tipo em `src/types.ts:1991`. Ele
  nunca vai para o Sell.

### O que isso significa

- Hoje, **todo estorno de venda do Sell deixa o acesso aberto**. Foi o que
  aconteceu na `…ybva`.
- A venda paga que não chega ao Sell (`…avqK`) é outro furo: compra sem acesso. A
  conciliação do #459 mostra os dois casos.
- A correção é a F2 da seção (c): **código novo e isolado no Financial**, que
  emite `purchase.refunded` com o mesmo contrato do `purchase.approved`
  (`seller_id`, `external_purchase_id = order.id`, assinatura `generic_hmac_v1`)
  quando o estorno de um pedido de checkout externo for confirmado. É a
  financial#150. **Nada muda no Sell**: o adaptador e o fulfillment já tratam o
  evento.
- Até lá, o gate do programa vale também para a venda sem divisão de pagamento:
  o estorno precisa de revogação manual, por runbook e com OK do Rodrigo, para
  cada caso que a conciliação mostrar.
