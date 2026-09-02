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

## Escopo — leia antes

Este runbook cobre **o que o Appsell guarda**. A conta de login vive no
Identity (`autonomia-identity-foundation`, Cognito) e é outro sistema. A
ordem importa: **se a pessoa continuar conseguindo entrar, o próximo login
recria o e-mail e o nome** — `sync_identity` (0048) faz upsert a cada
callback — e desfaz a anonimização feita aqui. Por isso o passo 3 vem antes
do 4, e "concluído" só se registra depois dos dois.

O que ainda não existe, e é follow-up com issue própria: uma marca
(tombstone) por `issuer + sub` que `sync_identity` recuse, e revogação
administrativa de sessões do Appsell (hoje elas expiram pelo prazo da
sessão; não há comando para derrubá-las).

## 1. Listar pedidos em aberto

No Postgres de produção (container `sell-autonomia_postgres-native-90`, ver
[PRODUCTION_RELEASE_APPSSELL.md](PRODUCTION_RELEASE_APPSSELL.md) para o acesso):

```sql
select pedido.created_at as pedido_em,
       pessoa.email_normalized as email,
       pessoa.display_name,
       pedido.actor_identity_user_id as identity_user_id,
       pessoa.identity_issuer, pessoa.identity_sub,
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

## 2. Confirmar e triar

1. Responder à pessoa pelo e-mail da conta confirmando o recebimento. Não
   prometa prazo: a política de privacidade não fixa um, e a LGPD não fixa
   prazo geral para eliminação — os 15 dias do art. 19, II, são para a
   *declaração completa* sobre o tratamento. Prazo é decisão do encarregado
   (contato@autonomia.site), não do operador.
2. Se `tem_loja` for verdadeiro, parar: a exclusão de vendedor é o fluxo de
   "Excluir tenant" da matriz de autorização, com retenção e janela de
   reversão próprias. Este runbook cobre só o comprador.
3. Se a pessoa quiser os dados antes, a exportação está na tela dela
   (`/settings/account`). Se a conta passar do teto (8 MiB), ver a seção
   "Exportação manual" abaixo.

## 3. Identity primeiro

Pedir à operação do Identity que **desative ou exclua o usuário** (`issuer` e
`sub` da consulta acima) no Cognito. Sem isso, nada do passo 4 dura: a pessoa
entra de novo e o callback recria e-mail e nome. Guardar a confirmação (data e
quem fez) para o registro do passo 5.

## 4. Dados no Appsell

**Antes de tocar em qualquer coisa: esperar o TTL da sessão** (60 min por
padrão, `sessionTtlMs` em `lib/redis-oauth-service.ts`) contado da
desativação no Identity. Uma sessão do Appsell já emitida continua válida até
expirar, e nesse intervalo a pessoa ainda grava progresso, marcadores e
anotações — limpar antes é limpar para ver reaparecer. Não há revogação
administrativa de sessão hoje (follow-up, ver "Escopo").

A regra final de retenção é a **D-018** (DECISION_REGISTER), ainda pendente
de DPO/Jurídico. Até ela existir, a régua mínima é esta — e o que ela não
cobre **não se toca sem OK do encarregado**:

- **Não se apaga** o que é prova de compra e de operação: `entitlement_grant`,
  `fulfillment_job`, `webhook_event`, `audit_event`. Têm retenção fiscal e
  contratual, e a tabela de auditoria é append-only de propósito.
- **Pode-se apagar** o que a pessoa criou e só a ela interessa: anotações e
  marcadores de PDF, `learning_pdf_study_state`, progresso de aula e estado
  de curso.
- **Anonimizar**: `identity_user.display_name` → `null`.
- **Revogar** acessos ativos: `buyer_entitlement.status` → `'revoked'`.
- **`identity_user.email_normalized`**: decisão da D-018. Não alterar sem OK
  do encarregado — nem apagar (pode ser exigido por registro de acesso) nem
  manter por reflexo.

Nenhuma dessas operações tem função pronta. Cada uma é um `psql` de
superusuário, feito à mão, num único caso por vez, com backup antes
(`pg_dump -Fc`), como qualquer escrita em produção.

## 5. Registrar que foi feito

Como superusuário (a tabela é append-only e a política de runtime não deixa).
O **ator é quem executou** — o `identity_user.id` do operador (o superadmin
tem linha em `identity_user`; ache-a pelo e-mail) — e a **pessoa vai em
`target_id`**. Gravar a pessoa como ator faria o registro afirmar que ela
mesma concluiu a exclusão, e é justamente essa distinção que a auditoria
existe para guardar. O `metadata` diz o que foi feito e quando o Identity
confirmou a desativação.

```sql
insert into audit_event(seller_organization_id,actor_identity_user_id,action,
  target_type,target_id,result,request_id,metadata)
values(null,'<identity_user_id do OPERADOR>','account.deletion.completed',
  'identity_user','<identity_user_id da PESSOA>','succeeded',gen_random_uuid(),
  jsonb_build_object('o_que','<resumo curto do que foi apagado/anonimizado/revogado>',
                     'identity_desativado_em','<data>','identity_por','<quem>'));
```

Depois disso a pessoa some da lista do passo 1 e a tela — se ela ainda
conseguir abri-la — volta a mostrar o botão; um pedido novo é um pedido novo.

## Exportação manual (acima do teto)

A tela recusa exportar contas acima de 8 MiB (`413`). O caminho manual é o
script [`scripts/ops/exporta-conta.sql`](../../scripts/ops/exporta-conta.sql):
espelha as seções da função `app_private.export_account_data` (migration
`0051`), recebe o `identity_user_id` **uma vez** pela variável do psql (nunca
por e-mail, que pode se repetir entre identidades), roda numa transação
somente leitura e aborta se o UUID não existir — um UUID errado não pode
produzir um documento "vazio" que pareça legítimo.

1. Confirmar a identidade da pessoa pelo e-mail da conta (não pelo pedido).
2. No container do Postgres, como superusuário:
   `psql -v sujeito='<identity_user_id>' -f exporta-conta.sql -o /tmp/conta-<uuid>.json`
   (o script liga `ON_ERROR_STOP`, `QUIET` e o formato só-JSON; a saída é um
   único documento, e o `json` da linha seguinte confirma:
   `python3 -m json.tool /tmp/conta-<uuid>.json >/dev/null`)
   e em seguida `chmod 600 /tmp/conta-<uuid>.json`.
3. Cifrar (`gpg -c`), entregar pelo canal combinado com a pessoa (nunca
   anexo em e-mail aberto), e apagar o arquivo local (`shred -u`) depois da
   confirmação de recebimento.
4. Só então registrar, como superusuário, um `audit_event`
   `account.data.exported` com o **operador como ator** e a **pessoa como
   alvo** (`target_type='identity_user'`).

O que **não** sai é o mesmo da função: `identity_sub`, `identity_issuer`,
hashes, `metadata` de auditoria, ids internos.

## O que a tela mostra

Enquanto houver pedido aberto, `/settings/account` mostra "Pedido de exclusão
registrado em …" e esconde o botão. O botão só volta depois do passo 5. Um
pedido que ficou meses aberto continua aparecendo — para a pessoa e nesta
lista — até alguém atendê-lo.
