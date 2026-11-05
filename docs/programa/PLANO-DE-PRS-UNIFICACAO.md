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
| 1 | financial | **F2** — avisar o estorno ao Sell | nada | Estorno e disputa precisam revogar o acesso (financial#150) |
| 2 | appsell | **S0** — teste de contrato do estorno vindo do Financial | nada (pode ir junto com F2) | Prova que o Sell revoga o formato exato que o F2 vai mandar |
| 3 | appsell | **S2** — taxa estritamente menor que o líquido e preço mínimo | nada | Sem isso o Financial recusa a sessão quando a taxa é igual ao preço |
| 4 | appsell | **S1** — chave `APPSELL_FINANCIAL_CONNECT_SELLER_IDS` | S2 | Envia conta conectada e taxa só para as lojas listadas |
| 5 | financial | **F1** — receptor Connect isolado | F2 (reaproveita o módulo de assinatura) | Só serve depois que existir loja Connect (S1) |
| 6 | appsell | **S3** — aviso no painel da loja | S1 | Mostra ao vendedor por qual conta a loja recebe |

F3 (endpoint Connect na Stripe) não é PR: é operação com OK separado (§3).

## 2. PRs

### F2 — Financial avisa o estorno ao Sell (financial#150)

- Objetivo: estorno e disputa de uma venda do Sell precisam revogar o acesso.
- O desenho e o código estão no Financial, que é privado: financial#150, #153 e
  #159. O worker nasce desligado; cada modo (`dry_run`, `deliver`) é ativação
  com OK separado (§3).
- O contrato com o Sell é o mesmo do `purchase.approved`: `event_type:
  "purchase.refunded"`, mesmo `seller_id`, `external_purchase_id = order.id` e a
  mesma assinatura. O `eventId` tem prefixo próprio, para o Sell não tratar como
  replay.
- Chargeback (disputa perdida) segue a decisão aberta em financial#150.

### S0 — Sell: teste de contrato do estorno do Financial

- Arquivo novo: `apps/api/tests/test_postgres_financial_refund_contract.py`.
- Monta o `purchase.refunded` exatamente como o F2 (cabeçalhos
  `x-external-checkout-webhook-*`, canonical v1, `eventId` com o prefixo novo)
  e prova que a compra aprovada com o mesmo `external_purchase_id` é revogada.
- Só teste. Se falhar, a correção entra neste PR e a Issue diz o motivo.

### S2 — Sell: taxa menor que o líquido e preço mínimo

- `lib/checkout-fee.ts` — hoje limita a taxa a `amountCents`. O Financial exige
  taxa menor que o valor. Preço igual à taxa vira 4xx no checkout. Passa a limitar em `amountCents - 1`.
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
  `CONTA-CONECTADA-DA-LOJA.md` §(c): rota nova, segredo próprio e casamento pela
  conta conectada do pedido. Ignora todo evento da conta de um gateway de
  operador, para não competir com o receptor atual.
- O endpoint Connect novo (F3) já nasce com os eventos de estorno. Estorno total
  de pedido entregue vira o mesmo aviso do F2, com o mesmo contrato. Assim as
  lojas com conta conectada própria não dependem do worker do F2.
- Arquivos novos no Financial, mais as linhas de registro (rota e configuração),
  que o PR mostra no diff.
- **O plano detalhado do F1 vem antes do código, com OK.** O receptor precisa
  reaproveitar a liquidação e a entrega do checkout externo sem alterar o que
  existe. O detalhe fica no Financial.

### S3 — Sell: aviso no painel

- `app/components/payout-connect-card.tsx` e
  `app/(protected)/settings/payouts/page.tsx` — texto informando se a loja recebe
  pela própria conta (está na lista) ou pela conta da plataforma. Sem `<select>`.
- Teste de componente para os dois estados.

## 3. O que fica fora dos PRs e exige OK separado

- Criar o endpoint Connect na Stripe (F3), já com `charge.refunded`/`refund.*`,
  e gravar o segredo dele.
- O endpoint Stripe atual do Financial **não muda** (decisão de 24/10/2026; ver
  `PLANO-F2-CONSULTA-STRIPE.md`).
- `FINANCIAL_EXTERNAL_CHECKOUT_REFUND_WORKER_MODE=dry_run` em produção. É a
  primeira execução: consulta a Stripe e não grava nem envia. Os números por
  operador vão para o Rodrigo.
- `FINANCIAL_EXTERNAL_CHECKOUT_REFUND_WORKER_MODE=deliver`. A primeira rodada
  alcança estornos antigos; a conferência do `dry_run` vem antes.
- Preencher `APPSELL_FINANCIAL_CONNECT_SELLER_IDS` com qualquer loja.
- Vincular a conta da Hub2You na `seller_payout_account`.
- Deploy de qualquer um dos PRs.

## 4. Riscos

- **Estorno que demora a aparecer.** O aviso depende do que a Stripe já
  confirmou. Um estorno pendente atrasa o aviso; o worker não inventa nada.
- **Primeira rodada em massa.** Mitigado pela conferência prévia da §3. O
  worker também tem um teto de itens por rodada.
- **Divergência de formato com o Sell.** Mitigado pelo S0, que usa o mesmo
  formato do teste do F2.
