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

Vale a partir do próximo evento de disputa daquela loja. Disputa anterior não é
reprocessada.

## Conferir

- `audit_event` com `action` `commerce.fulfillment_dispute_applied` ou
  `commerce.fulfillment_dispute_recorded`, e `metadata->>'reason'`.
- `fulfillment_job` dos três tipos de disputa sem `dead_letter`.
- Na conciliação (#147), nenhum acesso divergente da compra.

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

## Volta da migration

A descida da 0074 recusa enquanto existir evento de disputa, grant suspenso ou
perdido, acesso suspenso ou linha na chave por loja (apagaria a auditoria).
Com a regra já usada, a volta é desligar as lojas e reverter o código. A migration fica.
