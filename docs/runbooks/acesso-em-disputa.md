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
env. Só `apply_checkout_dispute` decide pela tabela; a conciliação só a lê,
para o alerta de compra suspensa em loja desligada. A api não lê nem escreve
nela (teste de Postgres).

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
  suspenso sem compra suspensa), `dispute_without_fulfillment` (evento de
  disputa sem job), `dispute_without_created_at` (evento de disputa sem a
  data real), `disputed_while_disabled` (compra suspensa em loja desligada) ou
  `dispute_while_switch_off` (disputa aberta ou perdida que chegou com a loja
  desligada e não foi registrada: decidir na mão antes de ligar a loja).

Mandar para fora só contagens. Nenhuma linha com comprador.

## Desligar uma loja

Também exige OK do Rodrigo:

```sql
insert into app_private.dispute_access_switch(seller_organization_id, action, actor, reason)
values ('<seller_id>', 'disable', '<quem>', '<motivo e referência da aprovação>');
```

Desligar **não devolve** os acessos já suspensos ou revogados por disputa, e
para de registrar disputa nova. O desfecho de uma disputa que já valia continua
sendo aplicado: quem ganhou depois do desligamento recupera o acesso. Enquanto
isso, a conciliação mostra cada compra suspensa em loja desligada
(`disputed_while_disabled`).

Religar depois de desligar move o piso para a nova linha `enable`: a disputa
aberta no intervalo fica sem efeito. A disputa já registrada mantém o que
tinha, e o desfecho dela continua valendo.

## Deploy e volta

Produção está em `0072_relatorio_de_vendas` (release de 08/11). Este PR entra
depois do #583 (`0069_motivo_do_acesso`), então o deploy sobe a cadeia
`0072 → 0069 → 0074` numa transação só, se as duas ainda não estiverem
aplicadas.

Quem executa é a Trilha 1 · Plataforma, com OK do Rodrigo em cada fase 🔴.
Procedimento de shell, túnel e credencial: o de `release-0064-api.md` (D3, o
migrador roda na máquina de quem opera, por `com_tunel`, a partir de um
checkout limpo do `SHA` mergeado; a credencial não vai ao host).

### Ordem

1. **Migration antes da api.** A api atual roda sobre o banco em 0074: a
   migration só acrescenta tabelas, funções e estados, e o `schema_ready` passa
   a aceitar a versão nova. O worker atual não tem o desvio de disputa, mas
   também não recebe evento de disputa, porque o ingresso só aceita os tipos
   novos depois da 0074 e o Financial só manda com o worker dele em `deliver`.
2. **Api depois**, pela ordem de update da **spec do host** (a api é
   `start-first`; ler `UpdateConfig.Order` antes, nunca supor pelo release
   anterior). Sem `--update-order` no comando.
3. **Financial (#165) por último**, com o worker de disputa desligado. Ligar
   o `deliver` para uma loja é passo separado, com OK do Rodrigo.

### Subir 🔴

```sh
cd "$CHECKOUT/apps/api"
export PGOPTIONS="-c lock_timeout=5s -c statement_timeout=120s"
PGOPTIONS="$PGOPTIONS -c default_transaction_read_only=on" \
  com_tunel .venv/bin/alembic current      # 0072_relatorio_de_vendas ou 0069_motivo_do_acesso
com_tunel .venv/bin/alembic upgrade head; echo "alembic rc=$?"
PGOPTIONS="$PGOPTIONS -c default_transaction_read_only=on" \
  com_tunel .venv/bin/alembic current      # 0074_acesso_em_disputa
```

ETA de 1 a 2 min. A 0074 também põe `lock_timeout = '5s'` antes das trocas de
checagem em `webhook_event`, `fulfillment_job`, `entitlement_grant` e
`buyer_entitlement`. Se o bloqueio não sair em 5 s, a transação volta inteira
(a 0069 junto) e o comando pode ser repetido.

Conferir: `alembic rc=0`, `alembic current` = 0074, `/health/ready` da api
**atual** verde depois da migration, e o smoke da Hub2You de
`storage-461-rede-interna.md`, antes e depois.

### Voltar a api

Pela imagem anterior guardada, com `docker service update --image`, como na
seção Rollback de `release-0064-api.md` (não `docker service rollback`). ETA
de 2 a 7 min. Voltar a api não mexe no banco: a api anterior roda sobre a 0074.

### Voltar o banco 🔴

Só se a causa for a própria 0074, com OK do Rodrigo, e com a api já na imagem
anterior. A descida recusa enquanto existir evento de disputa, estado de
disputa, grant suspenso ou perdido, acesso suspenso ou linha na chave por loja:
apagaria a auditoria. Com a regra já usada, a volta é desligar as lojas e
reverter o código; a migration fica.

```sh
cd "$CHECKOUT/apps/api"
export PGOPTIONS="-c lock_timeout=5s -c statement_timeout=120s"
com_tunel .venv/bin/alembic downgrade 0069_motivo_do_acesso; echo "rc=$?"
PGOPTIONS="$PGOPTIONS -c default_transaction_read_only=on" \
  com_tunel .venv/bin/alembic current      # 0069_motivo_do_acesso
```

ETA de 1 a 2 min; o `drop`/`add` das checagens pode precisar de repetição por
causa do `lock_timeout`. O alvo é a **anterior na cadeia** (`0069`), nunca
`-1` nem um número de outra trilha.

### Quem confere o quê

| O quê | Quem | Como |
|---|---|---|
| `alembic current` antes e depois | quem executa | comandos acima |
| `/health/ready` da api atual depois da migration | quem executa | `curl` pelo host |
| Smoke da Hub2You antes e depois | quem executa, e a Dinheiro lê | `storage-461-rede-interna.md` |
| Nenhum `dead_letter` de disputa e a conciliação vazia nas categorias de disputa | a Dinheiro | seção Conferir |
| O Financial com o worker de disputa desligado | a Dinheiro | switch do #165 em `off` |
