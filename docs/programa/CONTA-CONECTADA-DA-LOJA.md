# A conta conectada da loja: caminhos de hoje e como unificar

Refs #439, #440. Trilha 2 · Dinheiro. Só análise de código; nada em produção.

Sell em `origin/main`; Financial em `autonomia-financial` `origin/main` 7d68ed1,
lido sem alterar nada.

## O que a Orquestração verificou na Stripe (só leitura, 24/10/2026)

- O arranjo de chaves e contas dos dois operadores live foi conferido. O
  detalhe fica fora deste texto, que é público.
- Operador `<operador-A>`: as cobranças caem na **conta conectada `<conta-conectada-da-loja>`**
  (express; cobranças e repasses ativos). É cobrança direta via `Stripe-Account`,
  **sem application fee**.
- Operador `<operador-B>`: cobra na própria conta da Autonom.ia.
- A conta conectada "AUTONOM.IA" existe, mas não cobra.
- A conta da Hub2You também recebe o faturamento do Chat2You, que não sai do
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

## (b) O gateway do operador no Financial

- O gateway Stripe é cadastrado por admin no Financial, um por operador e modo.
  O `account_id` dele define em que conta caem as cobranças do operador.
- Vários fluxos do Financial dependem desse `account_id`: checkout externo do
  Sell, faturas, renovação, cartão salvo e crédito avulso. **Mudar o gateway do
  operador `<operador-A>` pode afetar esses fluxos e o faturamento do Chat2You na
  conta da Hub2You.**
- O receptor de webhook de hoje casa o evento pela conta do gateway. Um pedido
  com a conta de outra loja não é confirmado. Por isso o F1 é pré-requisito de
  qualquer loja na flag do S1.

O mapa com as referências de código fica no Financial, que é privado.

## (c) Proposta para unificar

Princípio: **a loja cadastra a própria conta pelo Sell, como já faz, e o Sell
manda essa conta e a comissão em cada pedido.** O Financial já aceita conta e
comissão no checkout externo. Falta o retorno: o webhook da conta conectada.

Operador: as lojas do autosserviço ficam sob um **operador compartilhado** da
plataforma, cujo gateway não tem `account_id`. A conta de cada loja vai por
pedido, nunca no gateway. A Hub2You continua no próprio operador (ver d).

| # | Mudança | Onde | Nova ou altera o que existe | Quem depende do que existe |
|---|---|---|---|---|
| S1 | No modo `financial_checkout`, resolver a conta da loja (`resolve_payout_destination`, que já existe), exigir `charges_enabled` e enviar `connect.accountId` e `applicationFeeAmountMinor` | Sell, `create-session/route.ts` | **Altera** código do Sell, **atrás de flag por loja** (`APPSELL_FINANCIAL_CONNECT_SELLER_IDS`, vazia por padrão) | Toda venda do Sell. Fora da flag, o pedido sai igual ao de hoje (`null`), byte a byte |
| S2 | Comissão menor que o líquido: o Sell hoje limita ao valor inteiro (`lib/checkout-fee.ts:23-26`) e o Financial recusa comissão igual ou maior que o valor. Preço mínimo ao publicar e teste do caso-limite | Sell | Altera o Sell | Só o caminho com flag |
| S3 | O aviso do painel sobre conta conectada passa a seguir o modo real do checkout | Sell, `dashboard-overview.tsx:244-282` | Altera o Sell (texto) | Nenhum |
| F1 | **Receptor novo de eventos Connect**: rota própria (ex.: `POST /financial/webhooks/stripe-connect`), segredo próprio do endpoint Connect da plataforma, e casamento pelo pedido. O pedido grava `selection.connect.accountId`, e o evento só vale se `event.account` for essa conta **e** o pedido for daquela sessão. Para a liquidação, chama o mesmo caminho do checkout externo | Financial | **Nova e isolada**. Não toca em `stripeEventMatchesGateway`, `resolveActiveStripeGateway` nem `company_gateways` | Eventos cujo `event.account` é o `account_id` de algum gateway (a Hub2You) **são ignorados** pelo receptor novo e seguem no endpoint de hoje. Assim nada muda para o operador `<operador-A>`. A idempotência vem do estado do pedido |
| F2 | Estorno e disputa de pedido conectado viram aviso ao Sell (com `seller_id`), para revogar o acesso | Financial | **Nova** (#151, financial#150) | Gate do programa: nada de divisão de pagamento em produção antes disso |
| F3 | Endpoint Connect na Stripe para a conta de plataforma, apontando para F1, com segredo próprio | Stripe e infra | Configuração nova | **Não pode reaproveitar** o segredo do gateway `<operador-A>`, se esse já for um endpoint Connect: seriam dois receptores do mesmo evento. Confirmar no painel antes |

**O que não muda em hipótese nenhuma**: o gateway e o `account_id` do operador
`<operador-A>`, o `account_id` nulo do `<operador-B>`, e as funções de casamento e de
escolha de gateway. Os dois tipos de conta convivem (express na Hub2You, sem
painel nas lojas do Sell), porque nos dois casos é cobrança direta.

Ordem: F1 e F3 → S1 e S2 com uma loja de teste na flag → F2 antes de qualquer
loja real → conciliação (#147/#459) → piloto com OK do Rodrigo. Isso é a #440.

## (d) A Hub2You no Sell, quando a Plataforma trouxer o operador da loja

- O Sell manda `operatorId: seller.financial_operator_id`
  (`create-session/route.ts:125`). Com a loja Hub2You vinculada ao operador
  `<operador-A>`, a venda do Sell cai **na conta conectada `<conta-conectada-da-loja>`**, pelo
  `account_id` do gateway, sem comissão. É o mesmo caminho do faturamento do
  Chat2You. **A Hub2You já é a vendedora de fato.**
- O webhook dessa venda casa no receptor de hoje, pela conta do gateway. O
  `purchase.approved` chega ao Sell como hoje.
- **Recomendação: a Hub2You fica exatamente assim.** Não entra na flag do S1, e
  nada muda no gateway dela.
- Se um dia houver comissão sobre a venda da Hub2You no Sell, o caminho só do
  Sell é mandar `connect.accountId` igual à mesma conta, mais a comissão. O
  Financial usa a conta do pedido ou, sem ela, a do gateway; a conta é a mesma e
  o webhook continua casando. Para isso a loja precisa da conta em
  `seller_payout_account`. Como a conta foi criada fora do Sell, isso exige um
  vínculo operacional (a função de vínculo existe, `0021:181`), com OK do
  Rodrigo. Não é para agora.
- A divergência real com a decisão da #439 está **no operador `<operador-B>`**:
  qualquer loja vinculada a ele vende na conta da Autonom.ia. Hoje nenhuma loja do
  Sell deveria estar nele. A flag do #469 fica vazia até F1 e S1 existirem.

## (e) Estorno e disputa

- Estorno e disputa de uma venda do Sell precisam revogar o acesso. O Sell já
  sabe revogar: o adaptador aceita `purchase.refunded`
  (`apps/api/src/appsell_api/webhook_adapters.py:14`) e o fulfillment trata o
  estorno (`0020_checkout_fulfillment.py:654-700`), mesmo fora de ordem. Testes:
  `tests/test_postgres_checkout_fulfillment.py:393,420,520`.
- O aviso de estorno vem do Financial: autonom-ia/autonomia-financial#150, #153 e
  #159. Pedido com conta conectada depende também do F1.
- A conciliação do #459 compara venda paga e acesso nos dois sentidos. Cada caso
  que ela mostrar segue por runbook, com OK do Rodrigo.
