# Plano de PRs — unificação Sell × Financial (conta conectada e estorno)

Refs #439 #440 · financial#150 · base: `CONTA-CONECTADA-DA-LOJA.md` §(c) e §(e).

Aprovação do Rodrigo (24/10/2026), em suas palavras: "Aprovo seguir com a
proposta de unificação: Sell atrás de chave desligada por loja, código novo e
isolado no Financial, Hub2You como está. Endpoint novo na Stripe e qualquer
ativação continuam com OK separado."

Este documento é o plano, **antes do código**. Nada aqui foi implementado.

## 0. Regras que valem para todos os PRs

- Cada PR tem uma Issue própria. O PR é pequeno e passa pela revisão da
  Qualidade. O merge só acontece com OK do Rodrigo, porque mexe em dinheiro.
- Financial (`autonom-ia/autonomia-financial`) só ganha arquivos novos. A única
  exceção é **linha de registro** em arquivo existente, listada abaixo PR a PR.
- Não mudam: `stripeEventMatchesGateway`, `resolveActiveStripeGateway`,
  `company_gateways`, o webhook Stripe atual (`processStripeWebhookEvent` e
  rota), o `PostgresStripeSettlementRecorder` e nenhuma migration existente.
- Prova no PR: o corpo do PR traz a saída destes comandos.

  ```sh
  git diff --stat origin/main...HEAD
  git diff origin/main...HEAD -- src/financial-service.ts src/gateway-client.ts src/verifiable-ledger.ts database/migrations/0[0-4]*.sql database/migrations/05[0-2]*.sql
  ```

  A segunda saída tem de vir **vazia**. O revisor confere arquivo por arquivo.
- Todo worker novo nasce **desligado** por variável de ambiente. Ligar é
  ativação: tem OK separado.
- Sem Stripe live, sem produção, sem segredo gravado, sem teste e commit no
  mesmo comando.

## 1. Ordem

| # | Repo | PR | Depende de | Por que nesta posição |
|---|---|---|---|---|
| 1 | financial | **F2** — emitir `purchase.refunded` ao Sell | nada | Há caso real (<cobrança-de-teste-2>): estornado e acesso ativo |
| 2 | appsell | **S0** — teste de contrato do estorno vindo do Financial | nada (pode ir junto com F2) | Prova que o Sell revoga o formato exato que o F2 vai mandar |
| 3 | appsell | **S2** — taxa estritamente menor que o líquido e preço mínimo | nada | Sem isso o Financial recusa a sessão quando a taxa é igual ao preço |
| 4 | appsell | **S1** — chave `APPSELL_FINANCIAL_CONNECT_SELLER_IDS` | S2 | Envia conta conectada e taxa só para as lojas listadas |
| 5 | financial | **F1** — receptor Connect isolado | F2 (reaproveita o módulo de assinatura) | Só serve depois que existir loja Connect (S1) |
| 6 | appsell | **S3** — aviso no painel da loja | S1 | Mostra ao vendedor por qual conta a loja recebe |

F3 (endpoint Connect na Stripe) não é PR: é operação com OK separado (§3).

## 2. PRs

### F2 — Financial emite `purchase.refunded` (financial#152)

> **Revisado em 24/10/2026: este desenho foi substituído.** O razão de produção
> não recebe estorno, porque os endpoints do Financial na Stripe não assinam
> `charge.refunded`/`refund.*`. O novo desenho, com o worker consultando a
> Stripe por uma bridge isolada, foi implementado no Financial (ver `PLANO-F2-CONSULTA-STRIPE.md`). O texto
> abaixo fica como histórico do #153 (draft).

**O que existe hoje (origin/main 7d68ed1):**

- `settleExternalCheckout` cria `financial.payments` com
  `gateway_payment_id = payment_intent` e `metadata.externalCheckoutOrderId`
  (`src/financial-service.ts:4480-4497`).
- O webhook atual já grava o estorno no razão: `PostgresStripeSettlementRecorder`
  acha o pagamento por `gateway_payment_id` e grava
  `financial.payment_settlement_events` com `event_type = 'refund'`
  (`src/verifiable-ledger.ts:962-1140`, tabela em `036:30-60`).
- Só `purchase.approved` vai ao Sell (`src/financial-service.ts:4509-4560`).

**Decisão de desenho:** um worker agendado novo lê o razão. Ele procura
pagamentos `external_hosted_checkout` com estorno **total** registrado e ainda
não avisados. Para cada um, envia `purchase.refunded` ao mesmo
`fulfillmentWebhookUrl` do pedido, com a mesma assinatura do `purchase.approved`.
O worker não chama a Stripe e não precisa de endpoint novo. Por isso também
alcança estornos já passados, como o <cobrança-de-teste-2> — desde que o razão tenha o estorno.

**Arquivos novos:**

- `database/migrations/053_external_checkout_refund_outbox.sql` — tabela
  `financial.external_checkout_refund_notifications` (pedido, pagamento, evento
  do razão, tentativas, estado, `delivered_at`). Chave única por pedido: um aviso
  por pedido, idempotente.
- `src/external-checkout-refund-signing.ts` — monta corpo e assinatura no formato
  `external-checkout.webhook.generic-hmac.v1`. Lê a mesma variável
  `FINANCIAL_EXTERNAL_CHECKOUT_WEBHOOK_SECRET_B64`, com a mesma validação.
- `src/external-checkout-refund-outbox.ts` — serviço: seleciona, reivindica com
  `FOR UPDATE SKIP LOCKED`, envia via `ExternalCheckoutFulfillmentTransport` (já
  exportado em `src/external-checkout-fulfillment.ts`, sem mudança), registra
  resultado e faz backoff.
- `src/external-checkout-refund-runner.ts` — handler agendado, no molde de
  `src/one-time-outbox-runner.ts`.
- `src/external-checkout-refund-workers.ts` — chave
  `FINANCIAL_EXTERNAL_CHECKOUT_REFUND_WORKER_ENABLED === "true"`. Padrão: desligado.
- `tests/external-checkout-refund-outbox.test.ts`,
  `tests/external-checkout-refund-runner.test.ts`,
  `tests/external-checkout-refund-outbox.postgres.test.ts`.

**Linhas de registro em arquivo existente (única alteração fora de arquivo novo):**

- `serverless.yml` — um bloco `externalCheckoutRefundRunner` com
  `schedule: rate(1 minute)`, igual ao `oneTimeCreditOutboxRunner` (`:274-284`).
- `config/financial-env.<stage>.yml` — nenhuma. O segredo já vem pelo
  `*financialEnvironment` (âncora em `serverless.yml:9`, variável em `:21`).
  A variável `FINANCIAL_EXTERNAL_CHECKOUT_REFUND_WORKER_ENABLED` entra só no bloco
  do runner novo, com padrão `"false"`.

**Contrato do aviso:**

```json
{
  "event_type": "purchase.refunded",
  "seller_id": "<selection.seller.id>",
  "external_product_id": "<selection.product.fulfillmentExternalProductId>",
  "external_purchase_id": "<order.id>",
  "occurred_at": "<occurred_at do evento de estorno no razão>",
  "buyer_email": "<igual ao approved>",
  "buyer_identity_sub": "<igual ao approved>"
}
```

`eventId = financial:external-checkout-refund:<order.id>`. Prefixo diferente do
`approved`, para o Sell não tratar como replay.

**Testes que o PR tem de trazer (cada um com o defeito que pega):**

1. Estorno total no razão gera um aviso, com assinatura válida. Pega formato
   divergente do `approved`.
2. Estorno parcial não gera aviso. Pega revogação indevida.
3. Rodar o worker duas vezes gera um envio. Pega duplicidade.
4. 5xx e 429 do Sell geram nova tentativa com backoff, respeitando `Retry-After`.
   4xx não-retentável vai para `dead_letter`. Pega laço infinito ou perda silenciosa.
5. Pagamento que não é `external_hosted_checkout` (assinatura, crédito avulso) é
   ignorado. Pega vazamento para outros produtos.
6. Chave desligada: o handler retorna `disabled` e não lê nada. Pega ativação
   por deploy.
7. Teste estrutural: `git diff` dos arquivos protegidos vazio (script no CI do
   PR ou checagem manual da Qualidade).

**Fora do F2, para decidir depois:** chargeback (disputa perdida) revoga
acesso? Hoje o razão grava `chargeback`, mas este PR só trata `refund`.

### S0 — Sell: teste de contrato do estorno do Financial

- Arquivo novo: `apps/api/tests/test_postgres_financial_refund_contract.py`.
- Monta o `purchase.refunded` exatamente como o F2 (cabeçalhos
  `x-external-checkout-webhook-*`, canonical v1, `eventId` com o prefixo novo)
  e prova que a compra aprovada com o mesmo `external_purchase_id` é revogada.
- Só teste. Se falhar, a correção entra neste PR e a Issue diz o motivo.

### S2 — Sell: taxa menor que o líquido e preço mínimo

- `lib/checkout-fee.ts` — hoje limita a taxa a `amountCents`. O Financial exige
  taxa `< net` (`financial-service.ts:3511-3514`). Preço igual à taxa vira 4xx no
  checkout. Passa a limitar em `amountCents - 1`.
- O preço mínimo publicável fica onde o produto é publicado. O arquivo exato
  será confirmado na Issue, antes do código.
- Testes: `tests/checkout-fee.test.ts` (preço 1, preço igual à taxa fixa,
  preço comum).
- Sem efeito em produção hoje: a taxa só é usada no caminho direto, que está
  fora do `financial_checkout`.

### S1 — Sell: chave `APPSELL_FINANCIAL_CONNECT_SELLER_IDS`

- `lib/financial-connect-allowlist.server.ts` (novo) — lê a variável. Lista
  separada por vírgula com `split`, sem regex. Vazia ou ausente: lista vazia.
- `app/api/checkout/stripe/create-session/route.ts` — no ramo
  `financial_checkout`, **somente** quando `seller.id` está na lista: resolve
  `resolveCheckoutPayoutDestination`, exige `charges_enabled` e envia
  `connect.accountId` e `applicationFeeAmountMinor`. Fora da lista, o código
  segue exatamente como hoje.
- Testes em `tests/stripe-create-session.test.ts`:
  - **chave vazia:** o corpo enviado ao Financial é idêntico, byte a byte, ao de
    hoje (`connect.accountId: null`, `applicationFeeAmountMinor: null`), e
    `resolveCheckoutPayoutDestination` não é chamado. Este é o teste que o
    Rodrigo exigiu;
  - loja fora da lista com a chave preenchida: idem;
  - loja na lista sem conta ou sem `charges_enabled`: 409, sem chamar o Financial;
  - loja na lista pronta: envia conta e taxa.
- `.env.example` e `docs/` — a variável documentada, vazia.

### F1 — Financial: receptor Connect isolado

- Só faz sentido depois do S1 ligado em alguma loja. Desenho em
  `CONTA-CONECTADA-DA-LOJA.md` §(c): rota nova (ex.:
  `POST /financial/webhooks/stripe-connect`), segredo próprio e casamento por
  `selection.connect.accountId`. Ignora todo evento cujo `account` é o
  `account_id` de algum `company_gateway`, para não competir com o webhook atual.
- **Estorno desde o início (24/10/2026).** O endpoint Connect novo assina
  `charge.refunded`, `refund.created` e `refund.updated` já na criação (F3). O
  tratamento é próprio, sem passar pelo `PostgresStripeSettlementRecorder` nem
  pelo ack do webhook atual (`src/financial-service.ts:7981-7990`):
  - casamento: o `event.account` tem de ser a conta conectada de um pedido
    (`selection.connect.accountId`), e o `payment_intent` do evento tem de ser
    o `gateway_payment_id` do `financial.payments` daquele pedido;
  - **charge desconhecido recebe ack 200**, com o evento registrado como
    `ignored_unknown_charge` no journal próprio do receptor e um contador no
    log. Nunca 503 por falta de par, porque esse é o defeito do caminho atual
    (`src/verifiable-ledger.ts:1028`);
  - 5xx só para falha transitória real (banco indisponível). A Stripe
    reentrega por até 3 dias, e depois o evento aparece no journal como falho;
  - idempotência por `event.id` no journal do receptor;
  - estorno total de pedido entregue entra na mesma tabela de avisos do F2, com
    a mesma assinatura, o mesmo event id e o mesmo envio. Estorno parcial e
    chargeback seguem a decisão aberta em financial#152.
  - Com isso, as lojas com conta conectada própria (S1) não precisam de
    polling. O polling do F2 cobre só os gateways diretos (`<operador-A>` e
    `<operador-B>`), filtrando `selection.connect.accountId` nulo.
- Arquivos novos: `src/stripe-connect-webhook.ts`,
  `tests/stripe-connect-webhook.test.ts`.
- Linhas de registro: uma rota em `src/routes.ts` e uma variável de segredo em
  `serverless.yml`/config. O PR mostra as duas no diff.
- **Plano detalhado do F1 vem em PR de docs próprio, depois do F2 mergeado.** O
  receptor precisa reaproveitar o settle e a entrega, e hoje os dois são
  métodos privados do `FinancialService`. Reaproveitar sem tocar nele exige uma
  análise que ainda não fiz. Não começo o F1 sem esse plano aprovado.

### S3 — Sell: aviso no painel

- `app/components/payout-connect-card.tsx` e
  `app/(protected)/settings/payouts/page.tsx` — texto informando se a loja recebe
  pela própria conta (está na lista) ou pela conta da plataforma. Sem `<select>`.
- Teste de componente para os dois estados.

## 3. O que fica fora dos PRs e exige OK separado

- Criar o endpoint Connect na Stripe (F3), já com `charge.refunded`/`refund.*`,
  e gravar o segredo dele.
- O endpoint Stripe atual do Financial **não ganha eventos de estorno** (decisão
  de 24/10/2026; ver `PLANO-F2-CONSULTA-STRIPE.md`).
- `FINANCIAL_EXTERNAL_CHECKOUT_REFUND_WORKER_MODE=dry_run` em produção. É a
  primeira execução: consulta a Stripe e não grava nem envia. Os números por
  operador vão para o Rodrigo.
- `FINANCIAL_EXTERNAL_CHECKOUT_REFUND_WORKER_MODE=deliver`. **Atenção:** o
  backfill revoga, na primeira rodada, todo estorno total de pedido entregue,
  inclusive o <cobrança-de-teste-2>.
- Preencher `APPSELL_FINANCIAL_CONNECT_SELLER_IDS` com qualquer loja.
- Vincular a conta da Hub2You na `seller_payout_account`.
- Deploy de qualquer um dos PRs.

## 4. Riscos

- **Estorno que não chega ao razão.** O `charge.refunded` cumulativo pode ficar
  pendente (`verifiable-ledger.ts:1144-1230`). O F2 só vê o que o razão
  registrou. Um estorno pendente atrasa o aviso, e o worker não inventa nada.
- **Primeira rodada em massa.** Mitigado pela conferência prévia da §3. O
  worker também tem um teto de itens por rodada.
- **Divergência de formato com o Sell.** Mitigado pelo S0, que usa o mesmo
  formato do teste do F2.
- **Duplicação da leitura do segredo** (`external-checkout-refund-signing.ts`
  repete a validação de `externalCheckoutWebhookSecret`, que é privada). Isso é
  escolha consciente para não tocar `financial-service.ts`. A unificação fica
  como Issue de dívida, para depois, com análise de impacto.
