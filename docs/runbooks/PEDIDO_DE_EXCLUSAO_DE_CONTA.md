# Pedido de exclusão de conta — como atender

- Origem: Minha conta (`/settings/account`), issue #351, Decisão 019.
- O que existe no banco: um `audit_event` por pedido, `action =
  'account.deletion.requested'`, `actor_identity_user_id` = a pessoa,
  `seller_organization_id` nulo, `request_id` do envelope, `created_at` = quando.
- O que NÃO existe: nenhuma exclusão automática. A conta, os acessos e as
  compras continuam inteiros até alguém executar este runbook.

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
    where feito.actor_identity_user_id=pedido.actor_identity_user_id
      and feito.action='account.deletion.completed'
      and feito.created_at>pedido.created_at)
order by pedido.created_at;
```

## 2. Antes de apagar qualquer coisa

1. Responder à pessoa pelo e-mail da conta, confirmando o recebimento e o
   prazo (a política de privacidade não promete prazo; a LGPD, art. 18 §5º,
   pede resposta "em prazo razoável" — use 15 dias como referência).
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

Como superusuário (a tabela é append-only e a política de runtime não deixa):

```sql
insert into audit_event(seller_organization_id,actor_identity_user_id,action,
  target_type,target_id,result,request_id,metadata)
values(null,'<identity_user_id>','account.deletion.completed','identity_user',
  '<identity_user_id>','succeeded',gen_random_uuid(),
  jsonb_build_object('executado_por','<quem>','o_que','<resumo curto>'));
```

Depois disso a pessoa some da lista do passo 1. Se ela pedir de novo, a tela
aceita (a janela de 30 dias do pedido conta a partir do último pedido, não da
conclusão).

## 4. O que a tela mostra

Enquanto houver pedido nos últimos 30 dias, `/settings/account` mostra "Pedido
de exclusão registrado em …" e esconde o botão. Passados 30 dias, o botão
volta — é o sinal para a operação de que o caso ficou parado.
