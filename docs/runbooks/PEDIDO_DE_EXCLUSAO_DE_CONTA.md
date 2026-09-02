# Pedido de exclusão de conta — como atender

- Origem: Minha conta (`/settings/account`), issue #351, Decisão 019.
- O que existe no banco: um `audit_event` por pedido, `action =
  'account.deletion.requested'`, `actor_identity_user_id` = a pessoa,
  `target_id` = a pessoa, `seller_organization_id` nulo, `request_id` do
  envelope, `created_at` = quando.
- O que NÃO existe: nenhuma exclusão automática. A conta, os acessos e as
  compras continuam inteiros até alguém executar este runbook.
- Um pedido fica **aberto** até existir um `account.deletion.completed`
  posterior com `target_id` = a pessoa. Não há prazo que o feche sozinho.

## 1. Listar pedidos em aberto

No Postgres de produção (container `sell-autonomia_postgres-native-90`, ver
[PRODUCTION_RELEASE_APPSSELL.md](PRODUCTION_RELEASE_APPSSELL.md) para o acesso):

```sql
select pedido.created_at as pedido_em,
       pessoa.email_normalized as email,
       pessoa.display_name,
       pedido.actor_identity_user_id as identity_user_id,
       (select count(*) from buyer_entitlement e
         where e.buyer_identity_user_id=pedido.actor_identity_user_id
           and e.status='active') as acessos_ativos,
       (select count(*) from app_private.entitlement_grant g
         where g.buyer_identity_user_id=pedido.actor_identity_user_id) as compras,
       exists(select 1 from seller_membership m
         where m.identity_user_id=pedido.actor_identity_user_id
           and m.status='active') as tem_loja
from audit_event pedido
join identity_user pessoa on pessoa.id=pedido.actor_identity_user_id
where pedido.action='account.deletion.requested'
  and pedido.result='succeeded'
  and not exists (
    select 1 from audit_event feito
    where feito.action='account.deletion.completed'
      and feito.result='succeeded'
      and feito.target_type='identity_user'
      and feito.target_id=pedido.actor_identity_user_id
      and feito.created_at>pedido.created_at)
order by pedido.created_at;
```

É a mesma regra que a função `read_account_status` usa para a tela — o que
esta consulta lista é exatamente o que a pessoa vê como "pedido registrado".

## 2. Antes de apagar qualquer coisa

1. Responder à pessoa pelo e-mail da conta confirmando o recebimento. A
   política de privacidade não promete prazo de eliminação, e a LGPD não fixa
   um prazo geral para ela — os 15 dias do art. 19, II, valem para a
   *declaração completa* sobre o tratamento, não para apagar. Não prometa
   prazo sem combinar com o encarregado (contato@autonomia.site).
2. Se `tem_loja` for verdadeiro, parar: a exclusão de vendedor é o fluxo de
   "Excluir tenant" da matriz de autorização, com retenção e janela de
   reversão próprias. Este runbook cobre só o comprador.
3. Se houver `compras`, a identidade **não é apagada**: a prova da compra
   tem retenção fiscal e contratual. O que se faz é anonimizar o que não
   precisa mais existir (nome, progresso, marcadores, anotações) e revogar os
   acessos, mantendo `identity_user` com o e-mail até o fim do prazo legal.
4. Sem compras e sem loja: pode-se revogar os acessos gratuitos e anonimizar
   o nome. O e-mail permanece pelo prazo do Marco Civil (registros de acesso).

Nenhuma dessas operações tem função pronta ainda. Cada uma é um `psql` de
superusuário, feito à mão, num único caso por vez, com backup antes
(`pg_dump -Fc`), como qualquer escrita em produção.

## 3. Registrar que foi feito

Como superusuário (a tabela é append-only e a política de runtime não deixa).
O **ator é quem executou** — o `identity_user.id` do operador (o superadmin
tem linha em `identity_user`; ache-a pelo e-mail) — e a **pessoa vai em
`target_id`**. Gravar a pessoa como ator faria o registro afirmar que ela
mesma concluiu a exclusão, e é justamente essa distinção que a auditoria
existe para guardar.

```sql
insert into audit_event(seller_organization_id,actor_identity_user_id,action,
  target_type,target_id,result,request_id,metadata)
values(null,'<identity_user_id do OPERADOR>','account.deletion.completed',
  'identity_user','<identity_user_id da PESSOA>','succeeded',gen_random_uuid(),
  jsonb_build_object('o_que','<resumo curto do que foi anonimizado/revogado>'));
```

Depois disso a pessoa some da lista do passo 1 e a tela volta a mostrar o
botão — se ela pedir de novo, é um pedido novo, com data nova.

## 4. O que a tela mostra

Enquanto houver pedido aberto, `/settings/account` mostra "Pedido de exclusão
registrado em …" e esconde o botão. O botão só volta depois do passo 3. Um
pedido que ficou meses aberto continua aparecendo — para a pessoa e nesta
lista — até alguém atendê-lo.
