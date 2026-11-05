# Acesso em compra com disputa de pagamento: ligar, conferir e desligar (#542)

A regra de acesso para disputa de pagamento vem da migration
`0074_acesso_em_disputa`. Ela vale só para as lojas ligadas em
`app_private.dispute_access_switch`, que nasce vazia.

A tabela é um histórico só de inserção: cada linha diz quem ligou ou desligou
a loja, quando e por quê, e a última linha da loja decide. Uma trigger recusa
`update` e `delete`: para desligar, insere-se uma linha `disable`.

**Por que tabela e não env.** A regra do programa: o liga-desliga por loja fica
em env (`APPSELL_*_SELLER_IDS`) quando a decisão é tomada no código da
aplicação, e em tabela quando é tomada dentro do SQL. Aqui a decisão acontece
dentro de `apply_checkout_fulfillment`, sob o lock da compra, e o SQL não lê
env. Só `apply_checkout_dispute` lê a tabela; a api não lê nem escreve nela
(teste de Postgres).

Loja fora da tabela: o evento de disputa é aceito e registrado, o job é
concluído com `dispute_access_disabled` e o acesso não muda.

## A regra

Cada evento de disputa traz o id da disputa na Stripe e a data em que ela foi
aberta lá. O Sell guarda o estado **de cada disputa** da compra em
`app_private.purchase_dispute`:

- o desfecho vence a abertura, em qualquer ordem de chegada, e a perdida vence
  a ganha;
- vale só a disputa aberta na Stripe **depois** da linha `enable` vigente da
  loja. A anterior fica registrada sem efeito (`effective = false`), com o
  motivo `dispute_before_enable`. Isso é fixado na primeira vez que a disputa
  chega e não muda depois.

Por compra, a partir das disputas que valem: alguma perdida, o grant fica
`charged_back` (final); senão alguma aberta, `suspended`; senão `active`. O
estorno vence a disputa e não troca `charged_back` por `refunded`.

O acesso do par (comprador, produto) segue os grants: `active` se algum está
ativo, `suspended` se algum está suspenso, `revoked` se não. A disputa ganha
cria o acesso que nunca existiu (a disputa chegou antes da aprovação) desde que
a aprovação já tenha sido aplicada.

Fora de escopo desta versão: a loja que cobra na própria conta conectada
(lista Connect). Essa disputa fica na conta da loja e é a #590.

## Antes de ligar

1. O Financial está mandando os eventos de disputa para o Sell. O worker de
   disputa do Financial está ligado em `deliver` para o operador da loja, com
   OK do Rodrigo.
2. A migration 0074 está aplicada e a api nova está no ar.

## Ligar para uma loja

Escrita em produção: exige OK do Rodrigo, uma loja por vez. Quem roda é a
Trilha 1 · Plataforma, no host, como superusuário do Postgres do Sell.

```sql
insert into app_private.dispute_access_switch(seller_organization_id, action, actor, reason)
values ('<seller_id>', 'enable', '<quem>', '<motivo e referência da aprovação>');
```

Vale para a disputa aberta na Stripe a partir desse momento. O primeiro
`deliver` do Financial relê disputas antigas (até 120 dias): elas chegam,
ficam registradas sem efeito e não mudam acesso nenhum.

## Conferir

- `audit_event` com `action` `commerce.fulfillment_dispute_applied` ou
  `commerce.fulfillment_dispute_recorded`, e `metadata->>'reason'`.
- `fulfillment_job` dos três tipos de disputa sem `dead_letter`.
- Na conciliação (#147), nenhuma linha de `disputed_with_access` (compra
  suspensa ou perdida com acesso ativo), `suspended_without_dispute` (acesso
  suspenso sem compra suspensa) ou `dispute_without_fulfillment` (evento de
  disputa sem job).

Mandar para fora só contagens. Nenhuma linha com comprador.

## Desligar uma loja

Também exige OK do Rodrigo:

```sql
insert into app_private.dispute_access_switch(seller_organization_id, action, actor, reason)
values ('<seller_id>', 'disable', '<quem>', '<motivo e referência da aprovação>');
```

Desligar **não devolve** os acessos já suspensos ou revogados por disputa. Eles
ficam como estão até o próximo evento de disputa daquela compra, ou até a
decisão de cada caso, com OK.

Religar depois de desligar move o piso para a nova linha `enable`: a disputa
aberta no intervalo fica sem efeito. A disputa já registrada mantém o que
tinha.

## Volta da migration

A descida da 0074 recusa enquanto existir evento de disputa, estado de
disputa, grant suspenso ou perdido, acesso suspenso ou linha na chave por loja
(apagaria a auditoria).
Com a regra já usada, a volta é desligar as lojas e reverter o código. A migration fica.
