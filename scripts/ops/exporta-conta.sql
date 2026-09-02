-- Exportação manual dos dados de UMA pessoa (Minha conta, #351).
--
-- Só para o caso em que a tela recusou por tamanho (413): o documento passa
-- de 8 MiB e a função `app_private.export_account_data` não o monta. Este
-- script espelha as seções da função, uma a uma, com o MESMO sujeito em
-- todas — o UUID entra uma vez, pela variável do psql, e nunca por e-mail
-- (que pode se repetir entre identidades). Transação somente leitura: nada
-- aqui escreve; o audit da entrega é gravado depois, à mão, pelo runbook.
--
-- Uso (superusuário, no container do Postgres de produção):
--   psql -v ON_ERROR_STOP=1 -v sujeito='<identity_user_id>' \
--        -f exporta-conta.sql -At -o /tmp/conta-<uuid>.json
--   chmod 600 /tmp/conta-<uuid>.json
-- Depois: gpg -c, entregar pelo canal combinado com a pessoa, shred -u.
--
-- O que NÃO sai é o mesmo da função: identity_sub, identity_issuer, hashes,
-- metadata de auditoria, ids internos.

\set ON_ERROR_STOP on
begin read only;

-- A pessoa tem de existir: sem esta linha, um UUID errado produziria um
-- documento "vazio" que parece um export legítimo de alguém sem dados.
-- 1/count(*): com a pessoa dá 1; sem ela, "division by zero" e o psql para
-- (ON_ERROR_STOP). Um `case ... 1/0` não serve — o planner dobra a constante.
select 1/count(*) as sujeito_existe
from public.identity_user where id=:'sujeito'::uuid;

select jsonb_pretty(jsonb_build_object(
  'schema_version',1,
  'exported_at',clock_timestamp(),
  'exported_by','manual',
  'identity',(select jsonb_build_object(
      'email',subject.email_normalized,
      'email_verified',subject.email_verified,
      'display_name',subject.display_name,
      'created_at',subject.created_at)
    from public.identity_user subject where subject.id=:'sujeito'::uuid),
  'memberships',coalesce((
    select jsonb_agg(jsonb_build_object(
      'store',seller.display_name,'store_slug',seller.slug,
      'role',membership.role,'status',membership.status,
      'since',membership.created_at) order by membership.created_at)
    from public.seller_membership membership
    join public.seller_organization seller on seller.id=membership.seller_organization_id
    where membership.identity_user_id=:'sujeito'::uuid),'[]'::jsonb),
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
    join public.seller_organization seller on seller.id=entitlement.seller_organization_id
    where entitlement.buyer_identity_user_id=:'sujeito'::uuid),'[]'::jsonb),
  'purchases',coalesce((
    select jsonb_agg(jsonb_build_object(
      'product',product.title,'store',seller.display_name,
      'status',grant_row.status,'occurred_at',grant_row.occurred_at)
      order by grant_row.occurred_at)
    from app_private.entitlement_grant grant_row
    join public.catalog_product product
      on product.seller_organization_id=grant_row.seller_organization_id
     and product.id=grant_row.catalog_product_id
    join public.seller_organization seller on seller.id=grant_row.seller_organization_id
    where grant_row.buyer_identity_user_id=:'sujeito'::uuid),'[]'::jsonb),
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
          and progress.enrollment_id=enrollment.id
          and progress.buyer_identity_user_id=:'sujeito'::uuid),'[]'::jsonb))
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
    where enrollment.buyer_identity_user_id=:'sujeito'::uuid),'[]'::jsonb),
  'pdf_bookmarks',coalesce((
    select jsonb_agg(jsonb_build_object(
      'product',product.title,'page_number',bookmark.page_number,
      'created_at',bookmark.created_at) order by bookmark.created_at)
    from app_private.learning_pdf_bookmark bookmark
    join public.buyer_entitlement entitlement
      on entitlement.seller_organization_id=bookmark.seller_organization_id
     and entitlement.id=bookmark.entitlement_id
     and entitlement.buyer_identity_user_id=bookmark.buyer_identity_user_id
    join public.catalog_product product
      on product.seller_organization_id=entitlement.seller_organization_id
     and product.id=entitlement.catalog_product_id
    where bookmark.buyer_identity_user_id=:'sujeito'::uuid),'[]'::jsonb),
  'pdf_annotations',coalesce((
    select jsonb_agg(jsonb_build_object(
      'product',product.title,'page_number',annotation.page_number,
      'tool',annotation.tool,'color',annotation.color,'width',annotation.width,
      'points',annotation.points,
      'created_at',annotation.created_at,'updated_at',annotation.updated_at)
      order by annotation.created_at)
    from app_private.learning_pdf_annotation annotation
    join public.buyer_entitlement entitlement
      on entitlement.seller_organization_id=annotation.seller_organization_id
     and entitlement.id=annotation.entitlement_id
     and entitlement.buyer_identity_user_id=annotation.buyer_identity_user_id
    join public.catalog_product product
      on product.seller_organization_id=entitlement.seller_organization_id
     and product.id=entitlement.catalog_product_id
    where annotation.buyer_identity_user_id=:'sujeito'::uuid),'[]'::jsonb),
  'emails_sent',coalesce((
    select jsonb_agg(jsonb_build_object(
      'kind',outbox.kind,'status',outbox.status,
      'created_at',outbox.created_at,'sent_at',outbox.sent_at)
      order by outbox.created_at)
    from app_private.email_outbox outbox
    where outbox.recipient_identity_user_id=:'sujeito'::uuid),'[]'::jsonb),
  'activity',coalesce((
    select jsonb_agg(jsonb_build_object(
      'action',event.action,'target_type',event.target_type,
      'result',event.result,'created_at',event.created_at)
      order by event.created_at)
    from public.audit_event event
    where event.actor_identity_user_id=:'sujeito'::uuid),'[]'::jsonb)
));

commit;
