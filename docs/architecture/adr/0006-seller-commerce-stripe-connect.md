# ADR 0006 — Seller commerce no Stripe Connect (provider da fronteira do ADR 0005)

- Status: Accepted with gates
- Data: 2026-07-24
- Decisores: Rodrigo / Financial / Integrações / AppSec
- Issue: [autonom-ia/appsell#140](https://github.com/autonom-ia/appsell/issues/140) (Part of [#96](https://github.com/autonom-ia/appsell/issues/96))
- Relaciona: revisita e especifica [ADR 0005](0005-jobs-webhooks-financial-boundary.md)

## Contexto

O ADR 0005 separou dois domínios (billing SaaS no Financial corporativo × seller
commerce via checkout externo + webhook no AppSell) e definiu um gate formal:
"só reconsiderar a fronteira quando o Financial tiver scoping principal→operator
certificado, APIs seller-scoped deny-by-default, testes cross-operator, threat
model de gateway/refund/chargeback/split/custódia e aprovação por novo ADR".

O ADR 0005 deixou o **provider** do checkout externo genérico ("checkout
externo"). O épico #96 experimentou rotear a venda seller→comprador pelo
`registration-checkout` do Financial (produto de teste `appsell-teste-checkout-r1`,
preço one_time). A verificação desta sessão (código + painel neuro.ai ao vivo)
confirmou que:

- o Financial é o motor de **assinatura** compartilhado por todos os produtos
  Autonom.ia/Hub2You (RDS compartilhado); não é ledger de marketplace e não faz
  split, payout por seller, comissão, conta por seller ou chargeback;
- rotear commerce por ele aumenta blast radius sobre terceiros no mesmo Financial;
- nenhum dos itens do gate do ADR 0005 foi satisfeito.

## Decisão

1. **Seller commerce (buyer → produto de um seller) permanece fora do Financial**
   e é a **direção de v1**, não uma etapa transitória a ser migrada.
2. **O provider do checkout externo do ADR 0005 é o Stripe (Connect).** O AppSell
   é a plataforma; cada seller opera sob uma conta conectada. A comissão da
   plataforma usa `application_fee`; payout, reembolso e chargeback são
   primitivos do Connect, projetados no ledger/entitlement do AppSell conforme
   ADR 0005 (webhook idempotente → outbox → `external_purchase` → entitlement).
3. **O Financial corporativo continua cobrando apenas o SaaS do AppSell** — o
   seller paga para usar a plataforma. O produto `sell` no Financial representa
   **essa assinatura de SaaS**, não a venda de curso.

| Domínio | Autoridade | Provider |
|---|---|---|
| Billing SaaS do AppSell | Financial corporativo (neuro.ai) | produto `sell` (assinatura do seller) |
| Seller commerce | Ledger/entitlements no AppSell | **Stripe Connect** + webhook |

## Consequências

- **`registration-checkout` do Financial não é o caminho da venda de curso.** O
  fluxo atual (#127/#139) que envia `customer`/`productPlanPriceId` ao Financial
  mira o alvo errado; o HTTP 400 observado deixa de ser um problema a resolver.
- **O caminho da venda é Stripe Connect + webhook** ingerido pelo AppSell (a
  máquina do ADR 0005: assinatura, idempotência, outbox, projeção comercial,
  entitlement, reconciliação). A #139 deve ser reavaliada nesse alvo.
- **Blocker crítico da venda passa a ser o fulfillment por webhook** (liberar
  acesso do comprador), não o contrato com o Financial.
- O produto de teste `appsell-teste-checkout-r1` no painel pertence à camada de
  billing e não deve ser tratado como o trilho de venda de curso.

## Alternativas rejeitadas

- **Usar o Financial atual para seller commerce** — reafirma a rejeição do ADR
  0005: scoping por operador não certificado, sem split/payout/comissão, blast
  radius sobre produtos de terceiros no RDS compartilhado.
- **Provider genérico "a definir"** — sem provider concreto, checkout, webhook e
  entitlement não têm contrato testável; Stripe Connect dá payout/comissão/split
  nativos e reduz superfície própria.
- **Migrar commerce para o Financial "no futuro" como premissa** — mantém acoplamento
  latente; a migração só volta à mesa se todos os itens do gate do ADR 0005 forem
  satisfeitos e aprovados por novo ADR.

## Validação obrigatória (além da do ADR 0005)

- onboarding de conta conectada não concede acesso a dados/mapping de outro seller;
- `application_fee`/split calculado no servidor, nunca a partir do browser;
- webhook do Stripe valida assinatura, janela temporal e idempotência antes de efeito;
- refund/cancel/chargeback do Connect seguem a política de revogação do ADR 0005;
- billing SaaS indisponível não revoga silenciosamente entitlement de buyer;
- contract test do Connect em modo test, sem dinheiro real, antes do piloto.

## Gate

Esta decisão não autoriza merge, deploy, produção, secrets, billing real, dados
de clientes ou criação de contas Connect reais. Cada slice (onboarding Connect,
checkout, webhook, entitlement, comissão) continua sujeito a Issue, review, testes
reais e aprovação de merge do Rodrigo.
