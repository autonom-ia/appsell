# AppSell — cadastro corporativo, preço e PIX

Data da verificação: 2026-07-13
Issue: #52
Ambiente: Local/CI e planejamento; nenhuma alteração de produção

## Veredito atual

O modelo comercial abaixo é uma proposta testável, não preço aprovado. O
produto corporativo deve permanecer `inactive`: o Admin ainda precisa fechar
autenticação/RBAC (issues #2, #3 e #4), o Financial precisa fechar identidade e
tenant derivado server-side (issue #7), e o fan-out Admin→Identity→Financial ainda
não oferece atomicidade, outbox nem reconciliação por read-back. Nenhum cadastro
no Neuro.ai e nenhum pedido à Stripe foi executado.

## Decisão de domínio

`appsell` é um único produto-plataforma corporativo no Admin/Identity/Financial.
Cursos, ofertas e produtos criados pelos sellers continuam source-of-truth no
AppSell e não são copiados para o catálogo corporativo do Neuro.ai.

Manifest canônico futuro, alinhado ao plano técnico, versionado também em
`docs/research/appsell-neuro-product.inactive.json` e ainda não aplicado.

`allowPasskeyLogin: true` (decisão #105): passkey é o fator forte do superadmin do
AppSell; o client habilita e-mail/senha e passkey e mantém Google/GitHub desligados.
Segue com host único de produção `https://sell.autonomia.site` — sem `localhost`,
sem `http:` e sem host de homologação. Cross-ref: foundation#41 (migration 022).

```json
{
  "key": "appsell",
  "name": "AppSell by Autonom.ia",
  "oauthClientId": "appsell-web",
  "allowedRedirectUris": ["https://sell.autonomia.site/auth/callback"],
  "allowedLogoutUris": ["https://sell.autonomia.site/auth/login"],
  "allowedOrigins": ["https://sell.autonomia.site"],
  "allowEmailPasswordLogin": true,
  "allowGoogleLogin": false,
  "allowGithubLogin": false,
  "allowPasskeyLogin": true,
  "allowBackgroundAuth": false,
  "status": "inactive"
}
```

Ativação exige, cumulativamente:

- Admin #2/#3/#4 com JWT fail-closed por JWKS, issuer e audience e RBAC por rota;
- Financial #7 sem decode inseguro, sem identidade por email e sem `operatorId`
  aceito do request; matriz negativa com dois operators e IDs cruzados;
- token comum → `403`, token forjado → `401`, organização alheia → `404`;
- outbox transacional, retry idempotente **e** estado persistido de cada destino;
- read-back convergente segundo o contrato de cada serviço: Admin (`id`, `key`,
  `oauthClientId`), Identity (`productId`, `productKey`, `oauth.clientId`) e
  Financial (`item.sourceId`, `item.key`), todos coerentes e inativos;
- callbacks, refresh, revogação e logout reais comprovados;
- KMS/staging e rollback testado.

## Preço proposto para validação

| Plano | Mensalidade | Taxa AppSell | Processamento |
|---|---:|---:|---|
| Launch | R$ 0 | 5,5% + R$ 0,49/venda | repassado a custo |
| Growth | R$ 99 | 3,5% + R$ 0,39/venda | repassado a custo |
| Scale | R$ 399 | 1,9% + R$ 0,29/venda | repassado a custo |

O repasse de processamento não é receita AppSell. A viabilidade jurídica e
contábil depende de quem é principal/agente e Merchant of Record, de como a
Stripe Connect liquida cada cobrança e de impostos, descontos, refunds,
disputas, inadimplência e custos compartilhados. Esses pontos são gates, não
premissas escondidas.

## Comparação indicativa de tarifas públicas

Estimativa parcial de tarifas para o seller, usando ticket de R$ 100 e preços públicos
observados em 2026-07-12. AppSell inclui sua taxa mais processamento Stripe
nacional estimado; não inclui impostos, Connect/payouts, streaming, refunds ou
disputas. Hotmart/Kiwify podem incluir componentes diferentes e, portanto, a
comparação não é contrato nem prova de margem.

Cada linha abaixo simula **um único seller tenant e uma única assinatura** no
mês, com a mensalidade aplicada exatamente uma vez. Não é uma coorte de 100
admins e os valores de tenants diferentes nunca podem ser somados antes da
escolha do plano ou da reconciliação.

| Cenário | Melhor AppSell + cartão | Melhor AppSell + PIX | Hotmart | Kiwify | Eduzz direta |
|---|---:|---:|---:|---:|---:|
| 10 vendas | Launch R$ 103,70 | Launch R$ 71,80 | R$ 109,00 | R$ 114,80 | R$ 73,90 |
| 100 vendas | Growth R$ 926,00 | Growth R$ 607,00 | R$ 1.090,00 | R$ 1.148,00 | R$ 739,00 |
| 1.000 vendas | Scale R$ 6.969,00 | Scale R$ 3.779,00 | R$ 10.900,00 | R$ 11.480,00 | R$ 7.390,00 |

Essa tabela compara cobrança ao seller; ela não produz margem AppSell. A tarifa
pública de 0,25% do Connect para plataformas que controlam o preço é apenas um
dos possíveis componentes. Não há teto de custo ou margem publicável antes de
decidir e reconciliar Merchant of Record, tipo de cobrança, controle de preço,
contas ativas, payouts, impostos, câmbio e o ledger fechado do tenant.

O posicionamento é deliberadamente **Pix-first**. Nas tarifas públicas listadas,
a proposta é mais barata que Hotmart e Kiwify nos três volumes e mais barata
que a Eduzz no PIX. No cartão, a taxa pública da Stripe faz a Eduzz direta
permanecer mais barata nos cenários de 10 e 100 vendas; o AppSell só a supera no
cenário Scale de 1.000 vendas. Hotmart e Kiwify oferecem tiers ou renegociação
por volume, portanto não devemos anunciar “menor taxa do mercado” sem restringir
método, volume, contrato e data.

Premissas públicas: Stripe cartão nacional 3,99% + R$ 0,39; PIX 1,19% e por
convite; Hotmart 9,9% + R$ 1 para vendas acima de R$ 20, com possível taxa
adicional de player; Kiwify 8,99% + R$ 2,49; Eduzz direta 4,9% + R$ 2,49 para
ticket acima de R$ 30. Tarifas de saque, antecipação, afiliados e reembolso não
foram normalizadas e podem mudar a comparação total.

Fontes:

- https://hotmart.com/pt-br/taxa-hotmart
- https://ajuda.kiwify.com.br/pt-br/article/quais-sao-as-taxas-da-plataforma-1ems3wq/
- https://stripe.com/br/pricing
- https://docs.stripe.com/payments/pix?locale=pt-BR
- https://ajuda.eduzz.com/hc/pt-br/articles/1260807397109-quais-s%C3%A3o-as-taxas-cobradas-pela-eduzz

## Gate de margem

Antes de custos reais, o teto matemático de custos de contribuição por tenant é:

| Cenário por tenant | Receita AppSell | Custo máximo para margem >50% | Contribuição mínima |
|---|---:|---:|---:|
| Launch, 10 vendas de R$ 100 | R$ 59,90 | R$ 29,94 | R$ 29,96 |
| Growth, 100 vendas de R$ 100 | R$ 488,00 | R$ 243,99 | R$ 244,01 |
| Scale, 1.000 vendas de R$ 100 | R$ 2.589,00 | R$ 1.294,49 | R$ 1.294,51 |

O teto não prova que os custos cabem nele. Ele define o máximo permitido para
infraestrutura, suporte, storage, impostos, perdas, Connect e demais custos de
contribuição somados, depois de eventual shortfall de processamento. O Launch
é o mais sensível: sem automação e limites de uso, R$ 29,94 por tenant/mês pode
ser insuficiente.

O simulador em `lib/appsell-pricing.ts` agora:

- avalia um único `tenantId` + `subscriptionId`, nunca uma coorte agregada;
- traz o plano contratado no próprio cenário e exige a mensalidade do catálogo
  exatamente uma vez, vinculada ao seu registro no ledger;
- declara timezone IANA, início/fim exclusivos do mês local, cutoff e política
  de fechamento; timestamps em UTC não substituem essa semântica local;
- exige snapshot do ledger de vendas fechado até o cutoff, com contagem, GMV e
  mensalidade coerentes, e vincula cada venda ao mesmo tenant/assinatura;
- rejeita venda fora da janela, tenant divergente e IDs de transação/ledger
  duplicados;
- versiona esse vínculo como contrato `2026-07-12.v3`/schema v3;
- aplica `floor` por transação à taxa percentual, favorável ao seller e
  explicitamente versionado como `floor_per_transaction`;
- usa `bigint` em multiplicações e somas e falha fechado antes de converter
  valores fora da faixa segura;
- compara margem `> 50%` exatamente (`2 × contribuição > receita`) e retorna a
  fração exata `contributionCents/platformRevenueCents`; não retorna bps
  truncado que possa parecer 50,00% quando o valor é apenas ligeiramente maior;
- calcula o teto inteiro máximo de custos por tenant que ainda preserva a
  desigualdade estrita, sem arredondar 50,00% para cima;
- gera SHA-256 canônico sobre contrato, tenant, assinatura, janela/cutoff,
  Connect, snapshots e o conjunto ordenado de vendas;
- exige que cada custo carregue o fingerprint, alocação por método, snapshot de
  custo, snapshot de vendas, tenant/assinatura, janela/cutoff, contagem e GMV;
- exige que a reconciliação vincule os dois snapshots já fechados;
- trata shortfall do processador como custo e ignora excedente de repasse.

Mesmo com tudo conciliado, a saída máxima é `model_eligible`: isso significa
que o cenário matemático superou 50%, não que a margem real foi certificada.
Custos `assumption`, `missing`, fora do contexto ou sem reconciliação mantêm o
resultado `unverified` ou fazem a avaliação falhar fechado.
O registro de reconciliação deve ser carregado server-side do ledger FinOps;
jamais pode ser aceito de payload do browser, seller ou admin.

Para aprovar preço comercial, FinOps deve reconciliar pelo menos cartão, PIX e
um mix conservador. O gate exige linhas distintas para taxa do volume Connect,
contas conectadas ativas, payouts, cross-border/câmbio, além de impostos,
disputas, refunds, fraude, streaming/storage, suporte, descontos e bad debt.
Zero também precisa de evidência contratada ou medida; não pode ser omissão.

`chargeType`, `merchantOfRecord` ou `pricingControl` como `undecided` mantém o
resultado `unverified`, mesmo que a aritmética supere 50%. A margem real só pode
ser declarada fora deste simulador depois do read-back do contrato Stripe e da
reconciliação server-side dos ledgers fechados.

Os ledgers devem ser carregados server-side. Campos de cobertura no payload são
contratos de domínio, não prova autônoma: somente snapshots persistidos,
fechamento FinOps e read-back do Financial podem produzi-los.

## Gaps do Financial e Neuro.ai

O contrato Financial atual aceita somente `monthly|yearly`. A issue Financial
#13 precisa acrescentar venda avulsa, taxa transacional versionada, método de
pagamento fail-closed, estados assíncronos do PIX, webhook idempotente e
ownership seller→operator/offer→plan-price tenant-scoped.

No Neuro.ai, os SDKs Admin/Financial estão defasados. A atualização deve ocorrer
em PR própria, com versões exatas publicadas; o atualizador global para `latest`
não deve ser usado porque também amplia Auth, Agents e Subscription. Depois,
testar login/logout, páginas Admin/Financial, BFF e permissões.

## Solicitação PIX — rascunho, não enviado

### Canal e elegibilidade confirmados

Para uma conta Stripe brasileira, o primeiro canal é o Dashboard autenticado:
`Configurações → Formas de pagamento → Pagamentos em tempo real`. A Stripe
informa que empresas em situação regular normalmente passam a se qualificar
depois de pelo menos 60 dias processando pagamentos. Se o PIX não aparecer e o
caso de uso for sólido, o canal correto é o suporte autenticado da própria
conta para pedir uma **reavaliação de risco PIX**; não há evidência de um email
público específico ou de que uma solicitação comercial contorne essa análise.

No Connect, são gates separados:

- PIX no Connect só é suportado quando a plataforma e as contas conectadas
  estão no mesmo país. Portanto, a topologia seller/connected account deve
  provar que ambas as partes estão no Brasil antes da habilitação; divergência
  de país bloqueia o PIX e mantém o checkout cartão-only;
- conta conectada com Stripe Dashboard: o titular habilita PIX nas próprias
  configurações de métodos de pagamento;
- conta Express ou sem Dashboard hospedado pela Stripe: a plataforma solicita
  a capability `pix_payments` para a conta conectada;
- a disponibilidade depende do Merchant of Record e do tipo de cobrança
  (`direct`, `destination` ou `separate charges and transfers`).

O contrato público consultado expressa o limite por PIX em **BRL** (R$ 0,50 a
R$ 3.000), mas expressa o limite agregado mensal por comprador/empresa como
**US$ 10.000**. Não é correto converter esse segundo valor em R$ 10.000 nem
hardcodar uma cotação. O checkout deve usar BRL por transação, tratar o limite
agregado conforme a resposta/política vigente da Stripe e permanecer
cartão-only quando a capability não estiver ativa. Este slice continua restrito
a pagamento PIX avulso; recorrência está fora do contrato implementado.

> Assunto: Solicitação de habilitação do PIX / capability `pix_payments`
>
> Somos a Autonom.ia e estamos preparando o AppSell, uma plataforma brasileira
> multitenant para creators venderem produtos digitais. Precisamos oferecer
> pagamentos avulsos em BRL por PIX. Nosso modelo aprovado será [Connect ou
> integração direta], com [tipo de cobrança], e o Merchant of Record será
> [entidade]. Solicitamos a avaliação da conta/plataforma para habilitação de
> PIX e, somente se o modelo aprovado usar connected accounts, da capability
> `pix_payments`. O fluxo não usará PIX para
> recorrência, setup/default payment method ou cobrança off-session e tratará
> confirmação, expiração e refund por webhooks idempotentes. Podemos fornecer
> CNPJ, domínio, volumes projetados, políticas de refund, KYC e arquitetura da
> integração no canal autenticado.

Enviar exige sessão autenticada da conta Stripe correta, CNPJ e decisão
documentada sobre Connect, tipo de cobrança e Merchant of Record. PIX possui
limites e regras próprias; a disponibilidade/capability deve ser confirmada na
conta. Nenhuma solicitação foi enviada nesta fase.

Fontes operacionais oficiais:

- https://support.stripe.com/questions/how-to-enable-pix-as-a-payment-method-in-brazil?locale=pt-BR
- https://dashboard.stripe.com/settings/payment_methods
- https://docs.stripe.com/payments/pix?locale=pt-BR
