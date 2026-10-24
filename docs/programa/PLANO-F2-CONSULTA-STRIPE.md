# F2 v2 — o worker de estorno consulta a Stripe

Refs #439 #487 · autonom-ia/autonomia-financial#152 · substitui o desenho do
autonom-ia/autonomia-financial#153 (draft)

Status: **plano, sem código.** Depende de OK do Rodrigo. O endpoint Stripe atual
do Financial **não é tocado**.

## 0. Por que o desenho do #153 não serve

O #153 lê o estorno no razão (`financial.payment_settlement_events`). Mas o
estorno só entra no razão por dois caminhos (origin/main 7d68ed1):

- o webhook Stripe: `src/financial-service.ts:3698-3703` → `src/verifiable-ledger.ts:962-1140`;
- a rota manual `POST /financial/internal/admin/settlement-events`
  (`src/routes.ts:1242-1256`, exige `FINANCIAL_ADMIN_COMMANDS_ENABLED`).

Não há consulta à Stripe em `src`. A Orquestração leu os dois endpoints do
Financial na Stripe, só leitura: eles assinam só `checkout.session.*`,
`payment_intent.*` e `setup_intent.succeeded`. Nenhum estorno de checkout chega
ao razão de produção. O dry-run do #153 daria 0 e não alcançaria o …ybva.

Assinar `charge.refunded`/`refund.*` no endpoint atual foi descartado. O estorno
de um charge que o Financial não conhece lança 503
(`src/verifiable-ledger.ts:1028`). O ack depois de falha só vale para
`payment_intent`/`setup_intent` (`src/financial-service.ts:7981-7990`). Isso
deixa o endpoint do checkout falhando sem fim, e ainda passa a ajustar
comissões de todos os pagamentos (`src/verifiable-ledger.ts:461`, `:1861`).

## 1. Escopo

O polling cobre só os **gateways diretos**: pedidos de checkout externo com
`selection.connect.accountId` nulo. Hoje são os dois operadores em produção:

| Operador | Cobrança | Chave | `Stripe-Account` |
|---|---|---|---|
| `13a30647…` | direct charge na conta conectada "Hub2You LTDA" | a do gateway (plataforma Autonom.ia) | `company_gateways.account_id` da Hub2You |
| `bf9daec0…` | na conta Autonom.ia | a do gateway (plataforma Autonom.ia) | `company_gateways.account_id` do gateway |

As lojas novas com conta própria (S1, `selection.connect.accountId` preenchido)
não entram no polling. Elas recebem o estorno pelo endpoint Connect novo do F1
(ver `PLANO-DE-PRS-UNIFICACAO.md`, F1).

## 2. Chave e conta usadas, por operador

**Regra única:** a consulta usa exatamente a mesma chave e a mesma conta que
criaram a Checkout Session do pedido. É o que o Financial já faz em
`src/financial-service.ts:3445-3456`:

1. `gatewayId = order.metadata.paymentGateway.companyGatewayId` (`:3445`);
2. `gateway = company_gateways[gatewayId]`, conferindo
   `gateway.operator_id = order.operator_id`, `provider = 'stripe'` e chave
   presente (`:3446-3449`). Diferença deliberada: sem `companyGatewayId`, o
   checkout cai em `resolveActiveStripeGateway` (`:3446`); o worker **não**
   cai nele, pula o pedido;
3. chave: o SSM em `gateway.secret_key_parameter_name` (`:3450`);
4. conta: `selection.connect.accountId ?? gateway.account_id` (`:3455-3456` →
   `src/gateway-client.ts:386`), enviada como `Stripe-Account`
   (`src/gateway-client.ts:422`, `:461`).

Consequências:

- não existe chave nova, e não é preciso chave da Hub2You;
- o gateway usado é imutável depois do uso (trigger
  `guard_bound_company_gateway_mutation`, migration 020:91-118). O nome do
  parâmetro SSM não muda; a rotação troca só o valor;
- `company_gateways` é **só lida** (SELECT). Não há escrita, nem chamada a
  `resolveActiveStripeGateway` ou `stripeEventMatchesGateway`;
- pedido sem `companyGatewayId`, com gateway inexistente, de outro operador ou
  sem chave fica `skipped_gateway_unresolved`, sem chamada à Stripe e com um
  contador no log.

## 3. Componentes

### 3.1 Lambda bridge nova: `stripeRefundReader` (arquivo novo `src/stripe-refund-reader.ts`)

Ela existe porque as Lambdas do Financial rodam em subnets privadas sem egress
(`serverless.yml:62-68`). Só uma Lambda fora da VPC alcança `api.stripe.com`,
como o `stripeBridge` já faz (`vpc: null`). Ela é **nova**: o `stripe-bridge.ts`
existente não ganha nenhuma action.

Entrada, num formato só:

```json
{ "secretKeyParameterName": "...", "stripeAccount": "acct_...", "paymentIntentId": "pi_...", "startingAfter": null }
```

Comportamento:

- valida a entrada com métodos de string, sem regex:
  - `paymentIntentId.startsWith("pi_")`;
  - `stripeAccount` nulo ou `startsWith("acct_")`;
  - `secretKeyParameterName` começa com `/autonomia/<stage>/financial/gateways/`
    e termina com `/secret-key` (`startsWith`/`endsWith`). Isso recusa o
    `webhook-secret` e qualquer outro parâmetro;
  - tamanhos máximos.
  Entrada inválida recebe recusa e nenhuma chamada;
- **lê a chave no SSM ela mesma.** A chave nunca viaja no payload do invoke nem
  aparece em log;
- faz uma única chamada, sempre `GET`, com o path montado no código:
  `https://api.stripe.com/v1/refunds?payment_intent=<pi>&limit=100[&starting_after=<re_>]`.
  Não aceita path nem método vindos de fora;
- devolve só `{ data: [{ id, status, amount, currency, created }], hasMore }`
  e o status HTTP. Não devolve o corpo bruto, e-mail nem cartão;
- os erros da Stripe voltam como `{ status, code }`, sem a mensagem bruta.

### 3.2 IAM e rede da bridge

Ela recebe um **papel próprio**, e não o papel compartilhado do `provider`. O
papel compartilhado tem `ssm:PutParameter`/`ssm:DeleteParameter`,
`ec2:*NetworkInterface` e `lambda:InvokeFunction` (`serverless.yml:69-106`).

- No `resources:`, um `AWS::IAM::Role` novo `StripeRefundReaderRole`. A função
  aponta para ele com `role: StripeRefundReaderRole` (recurso nativo do
  Serverless v3, sem plugin e sem tocar em `package.json`).
- O que a política do papel permite:
  - `logs:CreateLogStream`, `logs:PutLogEvents` só no log group
    `/aws/lambda/autonomia-financial-${stage}-stripeRefundReader`
    (`logs:CreateLogGroup` no mesmo ARN);
  - `ssm:GetParameter`, **sem** `GetParameters`, `ByPath`, `Put` ou `Delete`, só
    nos nomes gerados por `gatewaySecretParameterName`
    (`src/secret-store.ts:87-90`):
    `arn:aws:ssm:<região>:<conta>:parameter/autonomia/${stage}/financial/gateways/*/stripe/*/secret-key`.
    Antes do deploy, a Orquestração confere, só leitura, que os nomes (não os
    valores) dos 2 gateways de produção seguem esse formato. Um gateway
    importado com outro nome ficaria fora e apareceria no dry-run como erro
    de leitura de chave;
  - `kms:Decrypt` com `kms:ViaService = ssm.${region}.amazonaws.com` e
    `kms:EncryptionContext:PARAMETER_ARN` igual ao mesmo ARN.
- O papel **não** permite `lambda:InvokeFunction`, `ec2:*`, SQS nem acesso ao
  banco.
- Rede: `vpc: null`. Uma Lambda fora da VPC não tem filtro de egress pela AWS;
  a contenção é o código (um host e um path fixos) e o papel mínimo.
- Concorrência: `reservedConcurrency: 2` e timeout de 15 s.
- Invocação: o papel compartilhado ganha **uma linha** em
  `lambda:InvokeFunction` com o ARN da `stripeRefundReader`. É a única mudança
  no IAM existente, aditiva, e o PR mostra o diff dela. Com isso, a `api`
  também passa a poder invocar a bridge. O risco é baixo, porque ela só lê
  estornos, e fica registrado.

### 3.3 Worker (o `externalCheckoutRefundRunner` do #153, com nova fonte)

O que fica igual ao #153:
- modo `off`/`dry_run`/`deliver`;
- allowlist de origens vazia por padrão;
- assinatura v1 e event id `financial:external-checkout-refund:<pedido>`;
- tabela de avisos, `dead_letter` para 4xx e backoff;
- vetor de ouro com o Sell (autonom-ia/appsell#494).

O que muda: a fonte do estorno deixa de ser o razão e passa a ser a bridge.

A migration 053 (ainda não mergeada) muda nesta revisão: além da tabela de
avisos, ganha `financial.external_checkout_refund_checks`, com estas colunas:
- `checkout_order_id` (PK, FK);
- `payment_id` (FK);
- `payment_intent_id`;
- `company_gateway_id` (FK);
- `stripe_account`;
- `refunded_minor`, a soma dos refunds com `status = 'succeeded'`;
- `refund_ids` (`jsonb`);
- `last_checked_at`, `next_check_at`, `last_http_status`, `check_failures`.

Ela só aceita SELECT/INSERT/UPDATE do `financial_api_runtime`, sem DELETE e
fora de nenhum outro schema. O banco é compartilhado com o auth.

Ciclo `deliver`, a cada 5 min:

1. Seleciona até `BATCH` (padrão 25) pedidos com `next_check_at <= now()`. São
   pedidos `provisioned`, com `externalFulfillmentEventId`,
   `selection.connect.accountId` nulo, origem na allowlist, pagamento em
   `financial.payments` com `metadata.kind = 'external_hosted_checkout'` e
   `paid_at` dentro do horizonte. O primeiro ciclo de cada pedido é o backfill.
2. Para cada um, resolve a chave e a conta (§2) e invoca a bridge, seguindo
   `hasMore` até 10 páginas.
3. Grava `refunded_minor` e `refund_ids`. Só `succeeded` conta; `pending`,
   `failed` e `canceled` não contam.
4. Se `refunded_minor >= round(payments.amount * 100)`, cria o aviso (tabela do
   #153), que segue o fluxo de entrega já testado.
5. Agenda o próximo `next_check_at` por idade do pagamento:

   | Idade | Intervalo |
   |---|---|
   | até 7 dias | 15 min |
   | 7 a 60 dias | 6 h |
   | 60 a 365 dias | 24 h |
   | mais de 365 dias | para de consultar |

6. Erro da bridge ou da Stripe: 429/5xx/timeout reagendam com backoff, sem
   aviso. 401/403/404 marcam `check_failures`, logam por operador e reagendam
   em 24 h. Um erro de chave ou de conta aparece no log sem travar os outros
   pedidos.

`dry_run`: faz os passos 1 a 3 **sem gravar nada**, com teto de 500 pedidos por
execução, e registra por operador:
- `checked`;
- `fullyRefunded`;
- `partiallyRefunded`;
- `skippedGatewayUnresolved`;
- `stripeErrors` por status;
- o prefixo de 12 caracteres do sha256 do `order.id` dos estornados.

A primeira execução em produção é o dry-run, e os números vão para o Rodrigo
antes do `deliver`.

**Por que não usar `/v1/events`.** A rota de eventos da conta devolve estornos
de todos os sistemas que usam a mesma conta Stripe, e tem retenção de cerca de
30 dias. A consulta por `payment_intent` só enxerga o charge do nosso pedido e
não tem limite de idade. O volume é pequeno e não justifica a exposição.

## 4. Testes

Teste por operador, o que a Orquestração pediu:

1. Pedido do operador com direct charge em conta conectada (fixture do tipo
   `13a30647`): a bridge recebe `stripeAccount` = `account_id` do gateway e o
   `secretKeyParameterName` do gateway do pedido.
2. Pedido do operador na conta da plataforma (fixture do tipo `bf9daec0`):
   `stripeAccount` = `account_id` desse gateway, e nunca o da Hub2You.
3. Dois pedidos de operadores diferentes no mesmo lote: cada invocação leva a
   chave e a conta do próprio gateway. Pega troca de credencial entre
   operadores.
4. Gateway de outro operador em `paymentGateway.companyGatewayId`: o pedido é
   pulado, sem chamada.
5. Pedido com `selection.connect.accountId` preenchido: fica fora do polling,
   porque é escopo do F1.
6. `dry_run` em produção: contador por operador. Um operador com 100% de
   `stripeErrors` 401/403 mostra chave ou conta errada antes do `deliver`.

Bridge:

- recusa um parâmetro SSM fora do prefixo, um id que não começa com `pi_` e uma
  conta que não começa com `acct_`, antes de qualquer chamada;
- faz só `GET`, só para `/v1/refunds`;
- segue a paginação até o teto;
- a chave não aparece no retorno nem no log (teste com logger espião);
- o corpo da Stripe é reduzido aos 5 campos.

Teste estrutural do `serverless.yml`:
- a `stripeRefundReader` tem `vpc: null`, `role: StripeRefundReaderRole` e
  `reservedConcurrency: 2`;
- o papel não tem `ssm:PutParameter`, `ssm:DeleteParameter`,
  `lambda:InvokeFunction` nem `ec2:`;
- o papel compartilhado tem só uma linha a mais.

PostgreSQL:
- seleção dos candidatos (connect nulo, horizonte, entregue, allowlist);
- agenda por idade;
- só `succeeded` conta;
- estorno total cria um aviso, parcial não cria;
- idempotência ao consultar de novo.

## 5. Prova de isolamento no PR

Igual ao #153. O diff destes arquivos vem vazio: `financial-service.ts`,
`gateway-client.ts`, `verifiable-ledger.ts`, `routes.ts`, `repository.ts`,
`stripe-bridge.ts`, `external-checkout-fulfillment.ts`, `config.ts` e as
migrations 001-052.

Mudanças em arquivo existente, todas listadas no PR:
- `serverless.yml`: o bloco do runner; o bloco da `stripeRefundReader`; o
  `StripeRefundReaderRole` em `resources`; uma linha em `lambda:InvokeFunction`;
- `pr-validation.yml`: o passo do teste PostgreSQL;
- `deploy-workflow.test.ts`: a linha da última migration.

## 6. OK separado (nada disso entra no PR)

- Deploy, que cria a Lambda e o papel novos.
- `dry_run` em produção e a leitura dos números.
- Preencher `FINANCIAL_EXTERNAL_CHECKOUT_REFUND_ENDPOINT_ORIGINS`.
- `deliver`. Na primeira rodada, o backfill revoga o …ybva.

## 7. Riscos

- **Chave da plataforma lendo conta conectada.** É o mesmo acesso que já cria a
  sessão (`src/financial-service.ts:3455-3456`). Não há permissão nova na Stripe.
- **Lambda nova com acesso ao SSM.** O papel é próprio, só com `GetParameter` no
  prefixo das chaves e sem escrita. A entrada é validada antes de ler o SSM.
- **Estorno feito depois do horizonte de 365 dias.** Não é visto. O horizonte é
  configurável e fica registrado.
- **Rate limit da Stripe.** Lote de 25 a cada 5 min, mais o backfill único. O
  volume atual é pequeno; 429 reagenda.

---

## Anexo — rascunho da Issue "lacuna do razão" (não abrir sem OK)

**Repositório:** autonom-ia/autonomia-financial

**Título:** O razão não recebe estornos da Stripe: comissões e dashboard ignoram reembolsos

**Corpo:**

Refs autonom-ia/appsell#487 · autonom-ia/autonomia-financial#152

### Problema

O razão verificável (`financial.payment_settlement_events`) já sabe gravar
estorno: `PostgresStripeSettlementRecorder`, `src/verifiable-ledger.ts:962-1140`.
Quando grava, ajusta as comissões de parceiro (`applySettlementCommissionAdjustments`,
`:461`, `:1861`), invalida payouts pendentes e registra clawback. O dashboard v2
mostra estornos (`src/product-dashboard-v2.ts:625`).

Os endpoints do Financial na Stripe, porém, não assinam `charge.refunded` nem
`refund.*` (leitura da Orquestração, 24/10/2026). O estorno só entra por
webhook (`src/financial-service.ts:3698-3703`) ou pela rota manual de admin
(`src/routes.ts:1242-1256`). Na prática, nenhum estorno chega ao razão.
Consequências:
- a comissão de parceiro sobre um pagamento estornado não é ajustada;
- o payout pode sair sobre dinheiro devolvido;
- o dashboard mostra receita sem os estornos.

Isso vale para todo produto que cobra pela Stripe do Financial (Chat2You, Sell e
outros), não só o Sell.

### Por que não basta assinar o evento

Um estorno de charge que o Financial não conhece lança 503
(`src/verifiable-ledger.ts:1028`). O ack depois de falha só cobre
`payment_intent`/`setup_intent` (`src/financial-service.ts:7981-7990`). O
endpoint passaria a falhar sem fim, o mesmo endpoint que entrega o
`checkout.session.completed`.

### Opções (decisão do Rodrigo)

1. Corrigir o ack para estorno sem par (gravar como `unmatched` e responder 200)
   e então assinar os eventos. Muda código existente do webhook e exige análise
   de impacto.
2. Conciliação por consulta à Stripe, no mesmo desenho da bridge do F2 v2, mas
   gravando no razão. Muda as comissões a partir da ativação.
3. Manter como está e documentar que comissão e dashboard não refletem estornos.

### Fora desta Issue

O estorno do Sell é tratado pelo F2 (autonom-ia/autonomia-financial#152), que
não grava no razão.
