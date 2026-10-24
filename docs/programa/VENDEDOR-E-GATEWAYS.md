# O vendedor é a loja: inventário, Stripe × Asaas e impacto na #440

Refs #439, #440. Trilha 2 · Dinheiro. Só análise: nada aqui muda código, nem do
Sell nem do Financial, nem produção.

## A decisão (Rodrigo, 24/10/2026)

> "o vendedor não é a autonomia. Precisamos deixar isso claro. Precisamos
> verificar quando usar stripe (mais para internacional) e quando usar o asaas
> dentro outras coisas."

> "Não podemos ter regressão do financial. Coisas para melhorar sem mexer ou
> atrapalhar o que existe vamos analisar e fazer."

Registro: <https://github.com/autonom-ia/appsell/issues/439#issuecomment-6382959301>.

Daqui em diante:

- O **vendedor perante o comprador, a bandeira do cartão e o fisco é a loja**
  (`seller_organization`). A Autonom.ia é a plataforma e fica com a comissão.
- No Financial, **código novo e isolado** pode ser feito. **Mudar o que existe**
  exige, antes, uma análise de impacto e o OK do Rodrigo, porque o Financial
  atende outros projetos em produção.

Todas as referências do Sell estão na `origin/main` deste repositório. As do
Financial estão em `autonomia-financial` `origin/main` 7d68ed1, lido sem
alterar nada.

---

## 1. Onde o Sell trata hoje a Autonom.ia como vendedora

Cada achado tem uma classe:

- **A**: mostra a Autonom.ia como vendedora.
- **B**: falta a identidade do vendedor onde o comprador precisa dela.
- **C**: encanamento que supõe a plataforma como vendedora.

### 1.0 De quem é a conta que recebe o dinheiro hoje

O operador é **por loja**, não global: o Sell manda
`operatorId: seller.financial_operator_id` (`create-session/route.ts:125`), que é
gravado por loja em `seller_organization`
(`0055_seller_financial_binding.py:105-111`), pelo
`configure_seller_financial.py` (o operador é um argumento explícito,
`--financial-operator-id`).

No Financial, a credencial do gateway também é **por operador**:

- `financial.company_gateways` tem uma linha por operador, provedor e modo
  (`UNIQUE (operator_id, provider, mode)`), com o **nome** do parâmetro SSM da
  chave secreta e o `account_id` (`database/migrations/002_payment_gateways.sql:14-33`).
- O nome do parâmetro é montado com o `operatorId`
  (`gatewaySecretParameterName`, `src/secret-store.ts:87`; uso em
  `src/financial-service.ts:787-791`).
- O checkout externo pega o gateway do operador do pedido
  (`resolveActiveStripeGateway(order.operatorId)`, `financial-service.ts:3445-3446`
  e `:4662-4674`), lê a chave pelo nome guardado (`:3450`) e cria a sessão com
  `accountId: gateway.accountId` (`:3453-3456`).
- Os operadores do Financial são **empresas**: a migration
  `010_operator_organization_link.sql:38-75` cria `autonom-ia` ("Autonom.ia") e
  `hub2you` ("Hub2You").

Consequência: **o dinheiro cai na conta Stripe que o gateway do operador da loja
usa.**

- Se a loja Hub2You do Sell está vinculada ao operador `hub2you`, e o gateway
  desse operador usa a conta Stripe da Hub2You, **a Hub2You já é a vendedora de
  fato**. Nesse caso, a divergência com a decisão é só de texto e de modelo
  (seções 1.2 a 1.6), não de dinheiro.
- Se a loja está vinculada ao operador `autonom-ia`, ou se o gateway do operador
  usa a conta Stripe da Autonom.ia, **a Autonom.ia recebe o dinheiro de fato**.
  Essa é a divergência principal com a decisão.
- Para **loja nova** a resposta já é conhecida, e é a segunda. O autosserviço do
  #469 (ainda não mergeado) grava o **mesmo par global** para toda loja
  (`APPSELL_FINANCIAL_SHARED_OPERATOR_ID`). Sem `connect.accountId`, toda loja
  nova cobraria na conta do gateway desse único operador. Por isso a flag do
  #469 fica vazia até a #440 mandar a conta conectada da loja.

**O código não responde qual é o caso da Hub2You hoje: só a produção.** Não li
dado de produção. A leitura que resolve, só de nomes, sem segredo nem
identificador completo:

```sql
-- Sell (appsell): a qual operador cada loja vinculada aponta
select slug, financial_operator_id from public.seller_organization where financial_operator_id is not null;
```

```sql
-- Financial: de que empresa é esse operador e se o gateway dele tem conta própria
select o.key, o.name, g.provider, g.mode, g.status, g.display_name, right(g.account_id, 4) as conta_final from financial.company_gateways g join financial.operators o on o.id = g.operator_id where g.operator_id = '<financial_operator_id da loja>';
```

Falta ainda saber **de que pessoa jurídica é a conta Stripe** por trás da chave
desse gateway. O banco não guarda isso; quem responde é o painel do Stripe ou o
Rodrigo.

### 1.1 Checkout

| Onde | O que acontece | Classe |
|---|---|---|
| `app/api/checkout/stripe/create-session/route.ts:105-117` | No modo `financial_checkout`, que é o de produção, o destino de repasse nunca é buscado e fica `null`. | C |
| `…/create-session/route.ts:120,150-153` | Por isso `connect.accountId` e `applicationFeeAmountMinor` vão sempre `null` ao Financial. A cobrança sai pela conta Stripe do gateway do **operador ao qual a loja está vinculada** (ver 1.0), sem conta conectada e sem comissão. | C |
| `…/create-session/route.ts:27-29` | O comentário do arquivo assume o desenho: "platform-direct, NOT Connect … charges the platform's own Stripe account". | C |
| `…/create-session/route.ts:110-117` | No caminho legado `local_stripe`, loja sem conta de recebimentos cobra pela plataforma (`destination = null`, sessão sem `stripeAccount`). Só bloqueia quando a conta existe com `charges_enabled = false`. | C |
| `…/create-session/route.ts:126-131` | O Financial recebe só `{id, slug, name}` da loja: o nome de exibição, sem nome legal nem documento. | B |
| `…/create-session/route.ts:200` | O item da sessão é só `product.title`, sem "vendido por". | B |
| `app/components/stripe-checkout-card.tsx:107` | O card de compra diz "Pagamento processado pelo Stripe", sem dizer quem vende. O nome da loja aparece só no sobretítulo (`:86`). | B |
| `app/components/checkout-workspace.tsx:181-191` | O checkout do cadastro mostra produto e "Checkout seguro", sem a loja. | B |
| `lib/checkout-fee.ts:13-26` | A comissão (`directChargeApplicationFeeCents`) existe, mas só é calculada quando há `destination`, o que no modo Financial nunca acontece. | C |

Em `app`, `lib` e `apps/api` não há `statement_descriptor`, `on_behalf_of` nem
`transfer_data`.

### 1.2 Recibo e e-mail

| Onde | O que acontece | Classe |
|---|---|---|
| `apps/api/src/appsell_api/email_layout.py:253,324` e `lib/email/layout-transacional.ts:94-95` | O rodapé diz "Autonom.ia Sell · enviado por Autonom.ia Tecnologia Ltda" com o CNPJ da Autonom.ia. É o único CNPJ que o comprador vê. | A |
| `apps/api/src/appsell_api/email_worker.py:63,212,214` e `config.py:18` | O remetente é "Autonom.ia Sell", `sell@autonomia.site`, também no Reply-To. A política (`docs/architecture/EMAIL_POLICY.md:31-38`) diz que "o vendedor nunca aparece como remetente", porque o domínio dele não assina DKIM pelo SMTP da plataforma. | A (remetente) |
| `email_layout.py:363-412` (`compra_confirmada`, `acesso_liberado`) | Os e-mails citam "da loja {loja}", só o nome de exibição. Não trazem valor, número do pedido, data, meio de pagamento nem identidade legal da loja. **Não é recibo.** | B |
| `apps/api/migrations/versions/0049_caixa_de_saida_de_email.py:84-89` | O payload da fila leva só `product_title`, `seller_display_name` e `seller_slug`. | B |
| `EMAIL_POLICY.md:54` | "Recibo ou comprovante da compra" está planejado, mas não existe. | B |
| `success_url` = `/library?checkout=success` | A biblioteca ignora o parâmetro: não há página de confirmação. | B |

### 1.3 Termos e privacidade

| Onde | O que acontece | Classe |
|---|---|---|
| `content/legal/TERMOS_DE_USO.md:40-42` | "A Autonom.ia não é … vendedora … A relação de consumo … se dá entre Vendedor e Aluno." | **correto** |
| `content/legal/POLITICA_DE_PRIVACIDADE.md:22-25` | A loja é controladora dos dados do aluno; a Autonom.ia é operadora. | **correto** |
| `TERMOS_DE_USO.md:103-108` | O §8 "Pagamentos, taxas e reembolsos" ainda é um texto provisório: falta dizer quem processa, qual a taxa, o prazo de recebimento e quem arca com estorno e chargeback. | B |
| `TERMOS_DE_USO.md:100-101` | O reembolso "segue a política do Vendedor, informada antes da compra", mas nenhuma tela mostra essa política. | B |
| `app/termos/page.tsx:4-5` | A descrição promete explicar "o repasse", e a cláusula não existe. | B |

### 1.4 Vitrine pública `/s/<loja>`

| Onde | O que acontece | Classe |
|---|---|---|
| `app/components/storefront-brand.tsx:63-68` | O rodapé de toda página da loja diz "Loja criada com Autonom.ia Sell / Pagamento e acesso protegidos pela plataforma.", o que sugere que a plataforma recebe o pagamento. | A/B |
| `app/s/[sellerSlug]/page.tsx:30` | A descrição padrão é "Compra pela loja oficial na Autonom.ia Sell". | A (ambíguo) |
| `lib/public-storefront.ts:42-54` | A loja pública tem nome de exibição e marca, mas nem nome legal, nem documento, nem contato. | B |
| Título, sobretítulo e autor do produto (`…/p/[productSlug]/page.tsx:38,114,143`) | Nomeiam a loja. | **correto** (nome de exibição) |

### 1.5 Texto para o vendedor

| Onde | O que acontece | Classe |
|---|---|---|
| `lib/payout-status.ts:40-41` | O texto diz "Conecte sua conta Autonom.ia de recebimentos" e "Conectar conta Autonom.ia". A conta Stripe é da loja, não da Autonom.ia. | A |

### 1.6 Modelo de dados

| Onde | O que acontece | Classe |
|---|---|---|
| `0001_domain_foundation.py:204-212`, `0052`, `0054` | `seller_organization` tem `slug`, `display_name`, `status` e marca. Não tem nome legal, documento nem país. | B |
| `0021_seller_payout_account.py:113-135` | A conta de recebimentos guarda `provider_account_id` e `charges_enabled`; o KYC fica só no Stripe. | B |
| `0020_checkout_fulfillment.py:96-178` | Não existe tabela de venda: `fulfillment_job` e `entitlement_grant` guardam o hash da compra e a loja, sem valor, moeda, gateway, comissão ou vendedor de registro. | B/C |
| `0055_seller_financial_binding.py:105-111` e `0059_canal_financial_global.py` | O vínculo com o Financial é operador + item do catálogo, com um canal global para todas as lojas. Não identifica o vendedor no gateway. | C |
| `lib/appsell-pricing.ts:16` | `MerchantOfRecord = "platform" \| "connected_account" \| "undecided"` ainda modela a decisão como aberta. | C |

Nenhuma migration tem `legal_name`, `cnpj`, `cpf`, `tax_id`, `country`,
`merchant`, `seller_of_record` ou `nota_fiscal`.

### 1.7 Proposta para o Sell

O que fazer, em ordem. Cada item vira Issue e PR próprios; nada aqui é código
ainda.

1. **Cobrar na conta da loja, nunca na da plataforma** (é a #440). No modo
   `financial_checkout`, a loja sem conta conectada pronta recebe um erro claro e
   não cobra. O fallback do `local_stripe` para a conta da plataforma
   (`route.ts:110-117`) também deixa de existir. A flag
   `APPSELL_FINANCIAL_ACTIVATION_REQUIRES_PAYOUT_ACCOUNT` do #469 passa a ser
   obrigatória antes de qualquer loja entrar no autosserviço.
2. **"Vendido por" em todo lugar onde o comprador decide ou paga**: card de
   compra, checkout do cadastro, página do produto e rodapé da vitrine. Texto
   proposto: "Vendido por {nome legal da loja} · {CPF/CNPJ mascarado}.
   Plataforma: Autonom.ia Sell." O rodapé da vitrine deixa de dizer que o
   pagamento é "protegido pela plataforma" e ganha links para os termos e para a
   política de reembolso da loja.
3. **Identidade legal da loja no modelo**: nome legal, tipo e número do
   documento, país e a fonte desse dado (KYC do Stripe ou cadastro do Asaas).
   A recomendação é ler do KYC do provedor e guardar só o necessário para exibir,
   com o documento mascarado.

   > **Autorizado pelo Rodrigo** (24/10/2026, no #487: "2. autorizo"): o Sell
   > guarda nome ou razão social, CPF/CNPJ e país da loja. O desenho está em
   > `IDENTIDADE-LEGAL-DA-LOJA.md`.
4. **Tabela de venda** com o vendedor de registro explícito: loja, gateway,
   conta conectada, valor bruto, comissão, moeda, estado (pago, estornado,
   contestado). Ela serve à conciliação (#147/#459) e ao recibo.
5. **Recibo de verdade** no e-mail de compra: valor, data, número do pedido,
   meio de pagamento e "Vendido por {loja}". O remetente segue sendo a
   plataforma (`EMAIL_POLICY.md`), mas o rodapé passa a dizer "A Autonom.ia é a
   plataforma, não a vendedora deste produto."
6. **Termos §8** escritos com o modelo comercial: comissão, prazo, estorno e
   chargeback. É texto jurídico e precisa do Rodrigo.
7. **Texto para o vendedor**: "a conta de recebimentos da sua loja" em vez de
   "conta Autonom.ia" (`lib/payout-status.ts`).
8. **Modelo de preço**: `MerchantOfRecord` passa a ser só
   `"connected_account"`; os cenários com `"platform"` saem dos testes.

---

## 2. Stripe × Asaas

### 2.1 O que o Financial já suporta hoje

| Capacidade | Stripe | Asaas |
|---|---|---|
| Implementado | Sim: `src/gateway-client.ts` | Sim: `src/asaas-gateway-client.ts`, atrás de flags desligadas por padrão (`FINANCIAL_ASAAS_ENABLED`, `_PIX_ENABLED`, `_CARD_ENABLED`, `_PIX_AUTOMATIC_ENABLED`, em `src/config.ts:53-57`) |
| **Checkout externo, o que o Sell usa** | **Sim, e só ele.** `resolveActiveStripeGateway` aceita só Stripe (`financial-service.ts:4662-4674`); `createExternalHostedCheckoutSession` recusa outro provedor (`:3447`) | **Não.** Só nos fluxos internos de outros projetos: `startCheckout`, fatura e renovação de assinatura |
| Moeda | O cliente repassa a moeda, mas o checkout externo aceita **só BRL** (`routes.ts:471`, `financial-service.ts:3504`) | Só BRL (`assertBrl`, `asaas-gateway-client.ts:301,337,560`) |
| Recorrência no checkout externo | Só compra única (`routes.ts:472`, `financial-service.ts:3503`) | — |
| País do comprador | Não há tratamento (nenhum `country` em `src`) | Não há |
| Cartão nacional | Sim, no checkout externo: `payment_method_types: ["card"]` (`gateway-client.ts:371`) | Sim, por checkout hospedado (`asaas-gateway-client.ts:561-566`), fora do checkout externo |
| Cartão internacional | Funciona como qualquer cartão na Checkout Session, mas o preço é sempre em BRL | Não encontrado |
| Pix | Não | Sim: cobrança, assinatura e Pix Automático (`asaas-gateway-client.ts:305,342,376`) |
| Boleto | Não | **Não** (nenhuma ocorrência de `boleto` ou `BOLETO` em `src`) |
| Split e comissão | Sim: Connect por **cobrança direta**, com cabeçalho `Stripe-Account` = conta conectada e `payment_intent_data[application_fee_amount]` (`gateway-client.ts:381-386`). Sem `transfer_data`, `on_behalf_of` nem onboarding de conta | **Não** (nenhum `split`, `walletId` ou subconta em `src`) |
| Validação da comissão | A taxa precisa ser inteira, ≥ 0 e **menor** que o líquido (`financial-service.ts:3511-3514`) | — |
| Prazo de recebimento | Não está no código: depende do contrato do provedor | Idem |
| Taxas do gateway | Não há tabela; o ledger registra `fee` vindo da conciliação (`verifiable-ledger.ts:1137-1139`) | Idem; o rollout exige conciliar bruto, taxa e líquido (`docs/asaas-rollout.md:88,110`) |
| Estorno feito pelo Financial | Não há chamada de estorno | Não; só `cancelPayment` (`asaas-gateway-client.ts:509-511`) |
| Estorno e disputa recebidos | `charge.refunded` e `charge.dispute.*` são mapeados só para crédito avulso (`financial-service.ts:8168-8176`). **No checkout externo, todo evento que não seja `checkout.session.completed` ou `async_payment_succeeded` é ignorado** (`:4176-4178`) | `PAYMENT_REFUNDED` e `PAYMENT_CHARGEBACK_*` são recebidos e deixados para a conciliação (`financial-service.ts:3906-3908`) |
| Aviso ao Sell | Só `purchase.approved` (`financial-service.ts:4518`) | — |
| Webhook | `POST /financial/webhooks/stripe`, com assinatura conferida (`routes.ts:2663`). Casa o evento pela conta: `event.account === gateway.accountId` (`financial-service.ts:8249-8252`) | `POST /financial/webhooks/asaas/:id`, um por gateway, com token em tempo constante (`asaas-gateway-client.ts:955-962`) |

### 2.2 Quando usar cada um

Critério proposto. Os números de taxa e prazo **não estão no código** e não
foram inventados: vêm do contrato vigente de cada provedor e precisam ser
medidos antes do piloto, na mesma régua das evidências de `lib/appsell-pricing.ts`.

| Situação | Gateway | Hoje no Financial | Código novo necessário |
|---|---|---|---|
| Comprador fora do Brasil, ou preço em outra moeda | **Stripe** | Não: o checkout externo é só BRL | Sim. Moeda além de BRL no checkout externo é **mudança no que existe** (`routes.ts:471`, `:3504`): análise de impacto + OK |
| Comprador no Brasil pagando com cartão internacional, preço em BRL | **Stripe** | Sim (cartão) | Não, além da #440 |
| Comprador no Brasil com cartão nacional | **Asaas** no alvo; **Stripe** hoje | Stripe sim; Asaas só fora do checkout externo | Sim: o Asaas no checkout externo |
| Pix | **Asaas** | Só fora do checkout externo | Sim: o Asaas no checkout externo |
| Boleto | **Asaas** | Não existe | Sim: boleto no cliente Asaas e no checkout externo |
| Comissão para a plataforma com o dinheiro na loja | Stripe: Connect com cobrança direta. Asaas: split para a carteira da loja | Stripe sim; Asaas não | Asaas: subconta (ou carteira) da loja e split. É código novo |
| Estorno e chargeback revogam o acesso | Os dois | Não, no checkout externo de nenhum dos dois | Sim nos dois (#151, financial#150) |

Regra de escolha proposta, quando os dois estiverem prontos para a loja:

1. **Moeda diferente de BRL ou comprador fora do Brasil**: Stripe.
2. **Pix ou boleto**: Asaas.
3. **Cartão em BRL**: o gateway que a loja tiver pronto. Com os dois prontos, a
   escolha é comercial (taxa e prazo medidos), por loja, não por produto.
4. **Sem gateway pronto para a loja**: não vende. Nunca cai na conta da
   plataforma.

Quem decide é o Financial, que já tem `financial.gateway_routing_rules` por
método, moeda, produto e plano (`database/migrations/047_asaas_multi_gateway.sql:37-57`).
O Sell não escolhe gateway; ele informa a loja e a conta conectada.

### 2.3 Caminho sem regressão no Financial

O checkout externo atende hoje a Hub2You em produção. Por isso:

- **Código novo e isolado**, que pode ser feito:
  - Um **receptor de eventos Connect separado**, com endpoint, segredo e casamento
    próprios, para os eventos com `event.account = acct_…` da loja. O
    `stripeEventMatchesGateway` existente não é tocado.
  - Um **checkout externo Asaas** como fluxo novo, ao lado do Stripe, ligado só por
    regra de roteamento da loja.
  - **Split no Asaas** e **boleto**, num módulo novo.
- **Mudança no que existe**, que exige análise de impacto e OK do Rodrigo:
  - Aceitar outra moeda ou outro provedor dentro de
    `startExternalCheckout` / `createExternalHostedCheckoutSession`.
  - Mexer em `resolveActiveStripeGateway`, que hoje falha com mais de um gateway
    Stripe ativo por operador.
  - Mudar `stripeEventMatchesGateway`.

---

## 3. Impacto na #440 (comissão), agora desbloqueada

A #440 segue o caminho que já era o de produção: **Stripe Connect via
Financial**. O Asaas não entra nela.

O que muda ou se confirma:

1. **O Sell envia a conta conectada e a comissão.** O
   `connect.accountId` vem da conta de recebimentos da loja (`0021`,
   `charges_enabled = true`), e o `applicationFeeAmountMinor` vem de
   `directChargeApplicationFeeCents` (`lib/checkout-fee.ts`). Loja sem conta
   pronta recebe um erro claro e não cobra.
2. **Divergência de limite que a #440 precisa resolver**: o Sell limita a
   comissão ao **valor inteiro** (`lib/checkout-fee.ts:23-26`, "the seller nets
   zero"), mas o Financial recusa comissão **≥** líquido
   (`financial-service.ts:3511-3514`). Num preço pequeno, em que a parte fixa
   (R$ 0,49 no plano launch) alcança o preço, o checkout falharia. Proposta: preço
   mínimo de venda acima da parte fixa, validado no Sell ao publicar o preço, e
   teste do caso-limite. O Financial não muda.
3. **Pré-requisito no Financial: eventos da conta conectada.** Com cobrança
   direta, `checkout.session.completed` chega com `event.account = acct_da_loja`.
   O casamento atual (`event.account === gateway.accountId`) recusa esse evento,
   e o comprador pagaria sem receber acesso. A #440 depende do receptor Connect
   novo da seção 2.3, que é código novo, sem tocar no casamento existente.
   Teste obrigatório: venda conectada da loja A nunca casa com pedido da loja B.
4. **Estorno e chargeback**: com cobrança direta, o estorno e a disputa acontecem
   na conta da loja, e o Financial hoje ignora esses eventos no checkout externo
   (`:4176-4178`). Vale o gate do programa: nada de divisão de pagamento em
   produção antes de o estorno revogar o acesso (#151, financial#150). O evento
   de estorno precisa levar `seller_id`, como já leva o `purchase.approved`.
5. **Plano da loja**: a comissão depende do plano (`lib/appsell-pricing.ts:7-9`:
   launch 5,5% + R$ 0,49; growth 3,5% + R$ 0,39; scale 1,9% + R$ 0,29), e o
   checkout usa `launch` por padrão. A decisão A2 (manter mensalidade ou cobrar
   só %) define se o plano entra na #440 ou fica `launch` no piloto.
6. **Conciliação antes de produção** (#147, #459): a venda conectada precisa
   aparecer com bruto, comissão e destino.
7. **Hub2You**: o vínculo atual não muda na #440. Se a Hub2You passar a cobrar na
   própria conta conectada, isso é uma migração com data e OK do Rodrigo, não um
   efeito colateral.

Tamanho estimado da #440 com o pré-requisito: Sell M, Financial M (receptor
Connect novo).

## Fora deste documento

- Números de taxa e prazo de Stripe e Asaas: vêm do contrato e ficam para a
  medição antes do piloto.
- Nota fiscal: com a loja como vendedora, a nota é da loja. A integração entra
  na decisão A6 ("o vendedor não precisa assinar outra ferramenta").
- Afiliados, repasse rápido e saldo: dependem da #440 e da A5.
