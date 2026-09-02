"""Minha conta do comprador: exportar os próprios dados e pedir a exclusão.

Não existia nenhuma rota de conta (#351). O comprador não tinha onde ver seus
dados nem exercer os direitos da LGPD — e o que a issue pedia ("grave em
`audit_event`, sem migration") não cabia no banco como ele é: a política
`audit_member_insert` (0001) só deixa o runtime escrever auditoria de quem tem
membership ativa numa loja, e o comprador puro não tem membership nenhuma.
Todo registro de auditoria deste projeto nasce dentro de uma função SECURITY
DEFINER; estas três são as do comprador.

Zero tabela nova. O pedido de exclusão é o próprio `audit_event`: append-only,
com ator, request_id e data — e é ele que a operação lê para executar a
exclusão manualmente (Decisão 019). Repetir o pedido dentro de 30 dias devolve
o registro existente, para um clique repetido não virar um pedido por clique.

A exportação devolve SÓ o que é do sujeito: identidade, memberships, acessos,
compras, progresso, marcadores e anotações (sem os traços do desenho, que são
o volume e não são dado pessoal), e-mails que mandamos e o que a pessoa fez.
Nunca entra: `identity_sub`, `identity_issuer`, hash de id externo, metadata
de auditoria (pode carregar dado de terceiro) nem id de outro sujeito.

Ordem de deploy: **esta migration ANTES da API** — o inverso da 0050. Função
nova sem chamador é inofensiva; a API nova sem as funções responderia 503 nas
duas rotas até a migration subir.
"""

from __future__ import annotations

from alembic import op

revision = "0051_minha_conta_do_comprador"
down_revision = "0050_recibo_do_link_de_turma"
branch_labels = None
depends_on = None

_EXPORT = "app_private.export_account_data(uuid,uuid)"
_DELETION = "app_private.request_account_deletion(uuid,uuid)"
_STATUS = "app_private.read_account_status(uuid)"

_READY_ATE_0050 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes',"
    "'0046_aula_de_meio_giga','0047_cota_de_armazenamento','0048_nome_de_quem_entrou',"
    "'0049_caixa_de_saida_de_email','0050_recibo_do_link_de_turma'))"
)
_READY_ATE_0051 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes',"
    "'0046_aula_de_meio_giga','0047_cota_de_armazenamento','0048_nome_de_quem_entrou',"
    "'0049_caixa_de_saida_de_email','0050_recibo_do_link_de_turma',"
    "'0051_minha_conta_do_comprador'))"
)

_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)

_REESCREVE = """
do $migration$
declare
  definicao text;
  reescrita text;
begin
  definicao := pg_get_functiondef('ALVO'::regprocedure);
  if (length(definicao) - length(replace(definicao, $de$DE$de$, ''))) / length($de$DE$de$) <> 1 then
    raise exception 'ALVO não continha a âncora exatamente uma vez' using errcode='55000';
  end if;
  reescrita := replace(definicao, $de$DE$de$, $para$PARA$para$);
  execute reescrita;
end $migration$;
"""

# A guarda é a mesma das outras funções do comprador (grant_public_product_access,
# 0031): capability exata, sujeito do contexto igual ao parâmetro, e NENHUM
# seller no contexto. O parâmetro existe para a chamada ser explícita e
# auditável; a autoridade é o contexto assinado.
_EXPORT_SQL = """
create function app_private.export_account_data(
  p_identity_user_id uuid,
  p_request_id uuid
) returns jsonb
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare
  subject public.identity_user%rowtype;
  exported jsonb;
begin
  if p_identity_user_id is null or p_request_id is null
     or app_private.context_text('capability') is distinct from 'account.export'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is not null then
    raise exception 'account export context is not authorized'
      using errcode='42501';
  end if;

  select * into subject from public.identity_user where id=p_identity_user_id;
  if not found then
    raise exception 'account export subject not found' using errcode='42501';
  end if;

  exported:=jsonb_build_object(
    'schema_version',1,
    'exported_at',clock_timestamp(),
    'identity',jsonb_build_object(
      'email',subject.email_normalized,
      'email_verified',subject.email_verified,
      'display_name',subject.display_name,
      'created_at',subject.created_at),
    'memberships',coalesce((
      select jsonb_agg(jsonb_build_object(
        'store',seller.display_name,'store_slug',seller.slug,
        'role',membership.role,'status',membership.status,
        'since',membership.created_at) order by membership.created_at)
      from public.seller_membership membership
      join public.seller_organization seller
        on seller.id=membership.seller_organization_id
      where membership.identity_user_id=p_identity_user_id),'[]'::jsonb),
    'accesses',coalesce((
      select jsonb_agg(jsonb_build_object(
        'product',product.title,'product_slug',product.slug,
        'store',seller.display_name,'store_slug',seller.slug,
        'status',entitlement.status,'source',entitlement.source,
        'starts_at',entitlement.starts_at,'expires_at',entitlement.expires_at,
        'progress_percent',entitlement.progress_percent,
        'created_at',entitlement.created_at) order by entitlement.created_at)
      from public.buyer_entitlement entitlement
      join public.catalog_product product
        on product.seller_organization_id=entitlement.seller_organization_id
       and product.id=entitlement.catalog_product_id
      join public.seller_organization seller
        on seller.id=entitlement.seller_organization_id
      where entitlement.buyer_identity_user_id=p_identity_user_id),'[]'::jsonb),
    'purchases',coalesce((
      select jsonb_agg(jsonb_build_object(
        'product',product.title,'store',seller.display_name,
        'status',grant_row.status,'occurred_at',grant_row.occurred_at)
        order by grant_row.occurred_at)
      from app_private.entitlement_grant grant_row
      join public.catalog_product product
        on product.seller_organization_id=grant_row.seller_organization_id
       and product.id=grant_row.catalog_product_id
      join public.seller_organization seller
        on seller.id=grant_row.seller_organization_id
      where grant_row.buyer_identity_user_id=p_identity_user_id),'[]'::jsonb),
    'learning',coalesce((
      select jsonb_agg(jsonb_build_object(
        'product',product.title,'course',course.title,
        'progress_percent',state.progress_percent,
        'completed_lessons',state.completed_lessons,
        'total_lessons',state.total_lessons,
        'enrolled_at',enrollment.created_at,
        'updated_at',state.updated_at,
        'lessons',coalesce((
          select jsonb_agg(jsonb_build_object(
            'lesson',lesson.title,'progress_percent',progress.progress_percent,
            'started_at',progress.started_at,'completed_at',progress.completed_at)
            order by progress.started_at)
          from public.lesson_progress progress
          join public.lesson
            on lesson.seller_organization_id=progress.seller_organization_id
           and lesson.id=progress.lesson_id
          where progress.seller_organization_id=enrollment.seller_organization_id
            and progress.enrollment_id=enrollment.id),'[]'::jsonb))
        order by enrollment.created_at)
      from public.learning_enrollment enrollment
      join public.catalog_product product
        on product.seller_organization_id=enrollment.seller_organization_id
       and product.id=enrollment.catalog_product_id
      join public.course
        on course.seller_organization_id=enrollment.seller_organization_id
       and course.id=enrollment.course_id
      left join public.course_learning_state state
        on state.seller_organization_id=enrollment.seller_organization_id
       and state.enrollment_id=enrollment.id
      where enrollment.buyer_identity_user_id=p_identity_user_id),'[]'::jsonb),
    'pdf_bookmarks',coalesce((
      select jsonb_agg(jsonb_build_object(
        'product',product.title,'page_number',bookmark.page_number,
        'created_at',bookmark.created_at) order by bookmark.created_at)
      from app_private.learning_pdf_bookmark bookmark
      join public.buyer_entitlement entitlement
        on entitlement.seller_organization_id=bookmark.seller_organization_id
       and entitlement.id=bookmark.entitlement_id
      join public.catalog_product product
        on product.seller_organization_id=entitlement.seller_organization_id
       and product.id=entitlement.catalog_product_id
      where bookmark.buyer_identity_user_id=p_identity_user_id),'[]'::jsonb),
    -- Sem `points`: o traço do desenho é volume, não dado pessoal (decisão
    -- reversível registrada na PR da #351).
    'pdf_annotations',coalesce((
      select jsonb_agg(jsonb_build_object(
        'product',product.title,'page_number',annotation.page_number,
        'tool',annotation.tool,'color',annotation.color,
        'created_at',annotation.created_at) order by annotation.created_at)
      from app_private.learning_pdf_annotation annotation
      join public.buyer_entitlement entitlement
        on entitlement.seller_organization_id=annotation.seller_organization_id
       and entitlement.id=annotation.entitlement_id
      join public.catalog_product product
        on product.seller_organization_id=entitlement.seller_organization_id
       and product.id=entitlement.catalog_product_id
      where annotation.buyer_identity_user_id=p_identity_user_id),'[]'::jsonb),
    'emails_sent',coalesce((
      select jsonb_agg(jsonb_build_object(
        'kind',outbox.kind,'status',outbox.status,
        'created_at',outbox.created_at,'sent_at',outbox.sent_at)
        order by outbox.created_at)
      from app_private.email_outbox outbox
      where outbox.recipient_identity_user_id=p_identity_user_id),'[]'::jsonb),
    -- Sem metadata: ela pode carregar dado de terceiro. Sem target_id: é id
    -- interno, e o alvo já está descrito por target_type.
    'activity',coalesce((
      select jsonb_agg(jsonb_build_object(
        'action',event.action,'target_type',event.target_type,
        'result',event.result,'created_at',event.created_at)
        order by event.created_at)
      from public.audit_event event
      where event.actor_identity_user_id=p_identity_user_id),'[]'::jsonb)
  );

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata)
  values(null,p_identity_user_id,'account.data.exported','identity_user',
    p_identity_user_id,'succeeded',p_request_id,'{}'::jsonb);

  return exported;
end $$
"""

_DELETION_SQL = """
create function app_private.request_account_deletion(
  p_identity_user_id uuid,
  p_request_id uuid
) returns table(requested_at timestamptz, already_requested boolean)
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare
  prior timestamptz;
begin
  if p_identity_user_id is null or p_request_id is null
     or app_private.context_text('capability') is distinct from 'account.deletion.request'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is not null then
    raise exception 'account deletion context is not authorized'
      using errcode='42501';
  end if;
  if not exists(select 1 from public.identity_user where id=p_identity_user_id) then
    raise exception 'account deletion subject not found' using errcode='42501';
  end if;

  -- Dois cliques ao mesmo tempo não podem virar dois pedidos: a leitura e o
  -- insert precisam ser um só passo por pessoa.
  perform pg_advisory_xact_lock(hashtextextended(
    'appsell.account-deletion:'||p_identity_user_id::text,0));

  select event.created_at into prior
  from public.audit_event event
  where event.actor_identity_user_id=p_identity_user_id
    and event.action='account.deletion.requested'
    and event.result='succeeded'
    and event.created_at>clock_timestamp()-interval '30 days'
  order by event.created_at desc
  limit 1;
  if found then
    return query select prior,true;
    return;
  end if;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata)
  values(null,p_identity_user_id,'account.deletion.requested','identity_user',
    p_identity_user_id,'succeeded',p_request_id,'{}'::jsonb)
  returning created_at into prior;
  return query select prior,false;
end $$
"""

# A tela precisa saber, ao abrir, se já existe pedido — e o runtime não lê
# audit_event de quem não tem loja (audit_member_select exige seller).
_STATUS_SQL = """
create function app_private.read_account_status(
  p_identity_user_id uuid
) returns table(deletion_requested_at timestamptz)
language sql stable security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
  select event.created_at
  from public.audit_event event
  where app_private.context_text('capability')='account.read'
    and app_private.context_uuid('identity_user_id')=p_identity_user_id
    and app_private.context_uuid('seller_organization_id') is null
    and event.actor_identity_user_id=p_identity_user_id
    and event.action='account.deletion.requested'
    and event.result='succeeded'
    and event.created_at>clock_timestamp()-interval '30 days'
  order by event.created_at desc
  limit 1
$$
"""


def upgrade() -> None:
    for sql in (_EXPORT_SQL, _DELETION_SQL, _STATUS_SQL):
        op.execute(sql)
    for signature in (_EXPORT, _DELETION, _STATUS):
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"grant execute on function {signature} to appsell_runtime")
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0050)
            .replace("PARA", _READY_ATE_0051)
        )


def downgrade() -> None:
    # Só as funções saem. Os audit_event que elas gravaram são registro de um
    # direito exercido e ficam — a tabela é append-only de propósito.
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0051)
            .replace("PARA", _READY_ATE_0050)
        )
    for signature in (_STATUS, _DELETION, _EXPORT):
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"drop function {signature}")
