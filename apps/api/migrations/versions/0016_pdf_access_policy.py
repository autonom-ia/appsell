"""Add publication-pinned PDF access policy.

Revision ID: 0016_pdf_access_policy
Revises: 0015_demo_zero_checkout
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_pdf_access_policy"
down_revision: str | None = "0015_demo_zero_checkout"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema_ready_sql() -> str:
    return f"""
        create or replace function app_private.schema_ready() returns boolean
        language sql stable security definer set search_path=pg_catalog,app_private as $$
          select to_regclass('public.identity_user') is not null
            and to_regclass('public.platform_grant') is not null
            and to_regclass('public.superadmin_bootstrap') is not null
            and to_regclass('public.seller_organization') is not null
            and to_regclass('public.seller_membership') is not null
            and to_regclass('public.audit_event') is not null
            and to_regclass('public.catalog_product') is not null
            and to_regclass('public.course') is not null
            and to_regclass('public.product_course') is not null
            and to_regclass('public.offer') is not null
            and to_regclass('public.publication') is not null
            and to_regclass('public.course_module') is not null
            and to_regclass('public.lesson') is not null
            and to_regclass('public.buyer_entitlement') is not null
            and to_regclass('public.learning_asset') is not null
            and to_regclass('public.publication_course') is not null
            and to_regclass('public.publication_lesson') is not null
            and to_regclass('public.learning_enrollment') is not null
            and to_regclass('public.lesson_progress') is not null
            and to_regclass('public.course_learning_state') is not null
            and to_regclass('public.asset_record') is not null
            and to_regclass('public.asset_blob') is not null
            and to_regclass('public.asset_version') is not null
            and to_regclass('public.lesson_asset_draft') is not null
            and to_regclass('public.publication_asset') is not null
            and to_regclass('public.checkout_integration') is not null
            and to_regclass('public.webhook_endpoint') is not null
            and to_regclass('public.external_product_mapping') is not null
            and to_regclass('public.webhook_event') is not null
            and to_regclass('public.demo_checkout_offer') is not null
            and to_regclass('public.demo_coupon') is not null
            and to_regclass('public.demo_order') is not null
            and to_regclass('public.demo_purchase_event') is not null
            and to_regclass('public.demo_purchase') is not null
            and to_regclass('app_private.outbox_event') is not null
            and to_regclass('app_private.bff_request_replay') is not null
            and to_regclass('app_private.seller_onboarding_request') is not null
            and to_regclass('app_private.product_draft_request') is not null
            and to_regclass('app_private.catalog_mutation_request') is not null
            and to_regclass('app_private.curriculum_mutation_request') is not null
            and to_regclass('app_private.demo_tenant_registry') is not null
            and to_regclass('app_private.learning_progress_request') is not null
            and to_regclass('app_private.asset_upload') is not null
            and to_regclass('app_private.asset_verification_job') is not null
            and to_regclass('app_private.asset_storage_cleanup_job') is not null
            and to_regclass('app_private.asset_mutation_request') is not null
            and to_regclass('app_private.asset_worker_identity') is not null
            and to_regclass('app_private.demo_inspection_ticket') is not null
            and to_regclass('app_private.demo_checkout_request') is not null
            and to_regclass('app_private.demo_checkout_outbox') is not null
            and to_regclass('app_private.demo_checkout_worker_identity') is not null
            and to_regprocedure('app_private.read_buyer_products(uuid)') is not null
            and to_regprocedure('app_private.read_buyer_seller_storefront(uuid,uuid)') is not null
            and to_regprocedure(
              'app_private.start_demo_inspection(uuid,text,text,integer,text,uuid)'
            ) is not null
            and to_regprocedure(
              'app_private.read_demo_inspection(uuid,text,uuid,text,uuid)'
            ) is not null
            and to_regprocedure(
              'app_private.revoke_demo_inspection(uuid,text,uuid,text,text,uuid)'
            ) is not null
            and to_regprocedure('app_private.read_public_storefront(text)') is not null
            and to_regprocedure('app_private.resolve_webhook_endpoint(text)') is not null
            and to_regprocedure(
              'app_private.accept_checkout_webhook(uuid,uuid,text,text,bigint,text,smallint,'
              'text,integer,text,text,smallint,text,smallint,timestamptz,bigint,uuid)'
            ) is not null
            and to_regprocedure('app_private.read_learning_player_v2(uuid,uuid,uuid)') is not null
            and to_regprocedure('app_private.read_learning_player_v3(uuid,uuid,uuid)') is not null
            and to_regprocedure(
              'app_private.set_lesson_asset_policy(uuid,uuid,uuid,uuid,uuid,uuid,uuid,'
              'text,boolean,bigint,uuid,text,uuid)'
            ) is not null
            and to_regprocedure(
              'app_private.list_lesson_asset_drafts_v2(uuid,uuid,uuid,uuid,uuid,uuid)'
            ) is not null
            and to_regprocedure(
              'app_private.authorize_publication_asset_download(uuid,uuid,uuid)'
            ) is not null
            and to_regprocedure('app_private.read_demo_checkout(uuid)') is not null
            and to_regprocedure(
              'app_private.complete_demo_zero_checkout(uuid,text,uuid,text,uuid)'
            ) is not null
            and to_regprocedure(
              'app_private.claim_demo_checkout_jobs(uuid,text,integer,integer)'
            ) is not null
            and to_regprocedure(
              'app_private.apply_demo_checkout_entitlement(uuid,text,uuid,uuid,bigint,uuid)'
            ) is not null
            and to_regprocedure(
              'app_private.fail_demo_checkout_job(uuid,text,uuid,uuid,bigint,text,integer)'
            ) is not null
            and exists(select 1 from pg_catalog.pg_trigger
              where tgrelid='public.lesson_asset_draft'::regclass
                and tgname='lesson_asset_draft_policy_valid' and not tgisinternal
                and tgenabled='O')
            and exists(select 1 from pg_catalog.pg_trigger
              where tgrelid='public.publication_asset'::regclass
                and tgname='publication_lesson_asset_limit' and not tgisinternal
                and tgenabled='O')
            and exists(select 1 from pg_catalog.pg_trigger
              where tgrelid='public.webhook_event'::regclass
                and tgname='webhook_event_immutable' and not tgisinternal
                and tgenabled='O')
            and exists(select 1 from pg_catalog.pg_trigger
              where tgrelid='public.demo_order'::regclass
                and tgname='demo_order_immutable' and not tgisinternal and tgenabled='O')
            and exists(select 1 from pg_catalog.pg_trigger
              where tgrelid='public.demo_purchase_event'::regclass
                and tgname='demo_purchase_event_immutable' and not tgisinternal and tgenabled='O')
            and exists(select 1 from pg_catalog.pg_trigger
              where tgrelid='public.demo_purchase'::regclass
                and tgname='demo_purchase_immutable' and not tgisinternal and tgenabled='O')
            and not exists(
              select 1 from unnest(array[
                'public.checkout_integration'::regclass,
                'public.webhook_endpoint'::regclass,
                'public.external_product_mapping'::regclass,
                'public.webhook_event'::regclass,
                'public.demo_checkout_offer'::regclass,
                'public.demo_coupon'::regclass,
                'public.demo_order'::regclass,
                'public.demo_purchase_event'::regclass,
                'public.demo_purchase'::regclass,
                'public.lesson_asset_draft'::regclass,
                'public.publication_asset'::regclass
              ]) relation(oid)
              join pg_catalog.pg_class class on class.oid=relation.oid
              where not class.relrowsecurity or not class.relforcerowsecurity
            )
            and exists(select 1 from public.alembic_version
              where version_num='{revision}')
            and exists(select 1 from app_private.context_signing_key where singleton)
            and exists(select 1 from app_private.bootstrap_trust_policy where singleton)
        $$
    """  # noqa: S608


def upgrade() -> None:
    statements = [
        """
        alter table lesson_asset_draft
          add column content_role text not null default 'course_content',
          add column download_allowed boolean not null default false,
          add constraint lesson_asset_draft_content_role_check
            check(content_role in ('course_content','support_material')),
          add constraint lesson_asset_draft_download_policy_check
            check(not download_allowed or content_role='support_material')
        """,
        """
        alter table publication_asset
          add column content_role text not null default 'course_content',
          add column download_allowed boolean not null default false,
          add constraint publication_asset_content_role_check
            check(content_role in ('course_content','support_material')),
          add constraint publication_asset_download_policy_check
            check(not download_allowed or (
              content_role='support_material' and media_type='application/pdf'
            ))
        """,
        """
        create function app_private.enforce_lesson_asset_draft_policy()
        returns trigger language plpgsql security definer
        set search_path=pg_catalog,public as $$
        begin
          if new.download_allowed and (
            new.content_role<>'support_material' or not exists(
              select 1 from public.asset_blob blob
              where blob.seller_organization_id=new.seller_organization_id
                and blob.id=new.blob_id and blob.media_type='application/pdf'
            )
          ) then
            raise exception 'download requires a support PDF' using errcode='23514';
          end if;
          return new;
        end $$
        """,
        "revoke all on function app_private.enforce_lesson_asset_draft_policy() from public,appsell_runtime",
        """
        create trigger lesson_asset_draft_policy_valid
        before insert or update of blob_id,content_role,download_allowed
        on lesson_asset_draft for each row
        execute function app_private.enforce_lesson_asset_draft_policy()
        """,
        """
        create or replace function app_private.library_context_allowed(
          p_identity_user_id uuid,p_capability text
        ) returns boolean
        language sql stable security definer
        set search_path=pg_catalog,app_private,public as $$
          select coalesce(p_identity_user_id is not null
            and p_capability in (
              'library.read','library.asset.read','library.asset.download'
            )
            and app_private.context_uuid('identity_user_id')=p_identity_user_id
            and app_private.context_uuid('seller_organization_id') is null
            and app_private.context_text('capability')=p_capability,false)
        $$
        """,
        """
        alter table app_private.asset_mutation_request
          drop constraint asset_mutation_request_operation_check,
          add constraint asset_mutation_request_operation_check check(operation in (
            'upload.create','upload.confirm','upload.cancel','upload.finalize',
            'asset.archive','asset.policy'
          ))
        """,
        """
        create function app_private.set_lesson_asset_policy(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,
          p_lesson uuid,p_asset uuid,p_content_role text,p_download_allowed boolean,
          p_expected_course_version bigint,p_idempotency uuid,p_body text,p_request uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare replay jsonb; draft lesson_asset_draft%rowtype;
          next_version bigint; result jsonb; changed boolean;
        begin
          if p_actor is null or p_seller is null or p_product is null or p_course is null
             or p_module is null or p_lesson is null or p_asset is null
             or p_content_role not in ('course_content','support_material')
             or p_download_allowed is null
             or (p_download_allowed and p_content_role<>'support_material')
             or p_idempotency is null or p_request is null or p_body is null
             or p_body!~'^[0-9a-f]{64}$' then
            raise exception 'invalid asset policy request' using errcode='22023';
          end if;
          if app_private.context_text('capability')<>'catalog.asset.policy.update'
             or app_private.context_uuid('identity_user_id') is distinct from p_actor
             or app_private.context_uuid('seller_organization_id') is distinct from p_seller then
            raise exception 'asset policy context is not authorized' using errcode='28000';
          end if;
          perform 1 from seller_organization organization
          join seller_membership membership
            on membership.seller_organization_id=organization.id
          where organization.id=p_seller and organization.status='active'
            and membership.identity_user_id=p_actor
            and membership.role in ('seller_owner','seller_admin')
            and membership.status='active'
          for share of organization,membership;
          if not found then
            raise exception 'asset policy actor is not authorized' using errcode='28000';
          end if;
          perform 1 from product_course linked
          where linked.seller_organization_id=p_seller
            and linked.catalog_product_id=p_product and linked.course_id=p_course
          for share;
          if not found then
            raise exception 'asset policy course is unavailable' using errcode='28000';
          end if;
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.asset/'||p_seller::text||'/asset.policy/'||p_idempotency::text,0));
          replay:=app_private.asset_mutation_replay(p_seller,'asset.policy',p_idempotency,
            p_actor,p_body,p_product,p_course,p_module,p_lesson,p_asset,null);
          if replay is not null then
            if replay->>'content_role' is distinct from p_content_role
               or (replay->>'download_allowed')::boolean
                 is distinct from p_download_allowed then
              raise exception 'asset policy idempotency conflict' using errcode='23505';
            end if;
            return replay||jsonb_build_object('applied',false,'replayed',true);
          end if;
          perform app_private.lock_curriculum_course(
            p_seller,p_course,p_expected_course_version
          );
          select candidate.* into draft from lesson_asset_draft candidate
          join asset_blob blob
            on blob.seller_organization_id=candidate.seller_organization_id
           and blob.id=candidate.blob_id
          where candidate.seller_organization_id=p_seller
            and candidate.catalog_product_id=p_product and candidate.course_id=p_course
            and candidate.module_id=p_module and candidate.lesson_id=p_lesson
            and candidate.asset_id=p_asset and candidate.status='active'
            and (not p_download_allowed or blob.media_type='application/pdf')
          for no key update of candidate;
          if not found then
            raise exception 'asset policy target is unavailable' using errcode='28000';
          end if;
          changed:=draft.content_role<>p_content_role
            or draft.download_allowed<>p_download_allowed;
          if changed then
            update lesson_asset_draft set content_role=p_content_role,
              download_allowed=p_download_allowed,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and catalog_product_id=p_product
              and course_id=p_course and module_id=p_module and lesson_id=p_lesson
              and asset_id=p_asset and status='active';
            update course set draft_version=draft_version+1,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_course
            returning draft_version into next_version;
          else
            next_version:=p_expected_course_version;
          end if;
          result:=jsonb_build_object('asset_id',p_asset,'content_role',p_content_role,
            'download_allowed',p_download_allowed,'course_version',next_version,
            'applied',changed,'replayed',false);
          perform app_private.record_asset_mutation(p_seller,'asset.policy',p_idempotency,
            p_actor,p_body,p_product,p_course,p_module,p_lesson,p_asset,null,result);
          insert into audit_event(seller_organization_id,actor_identity_user_id,action,
            target_type,target_id,result,request_id,metadata)
          values(p_seller,p_actor,'catalog.asset_policy_set','asset',p_asset,'succeeded',
            p_request,jsonb_build_object('course_id',p_course,'lesson_id',p_lesson,
              'previous_content_role',draft.content_role,
              'previous_download_allowed',draft.download_allowed,
              'content_role',p_content_role,'download_allowed',p_download_allowed,
              'course_version',next_version,'changed',changed));
          return result;
        end $$
        """,
        "revoke all on function app_private.set_lesson_asset_policy(uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,boolean,bigint,uuid,text,uuid) from public",
        "grant execute on function app_private.set_lesson_asset_policy(uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,boolean,bigint,uuid,text,uuid) to appsell_runtime",
        """
        create function app_private.list_lesson_asset_drafts_v2(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,p_lesson uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare result jsonb;
        begin
          perform app_private.lock_curriculum_access(
            p_actor,p_seller,p_product,p_course,'catalog.asset.read'
          );
          perform 1 from lesson where seller_organization_id=p_seller
            and course_id=p_course and module_id=p_module and id=p_lesson and status='active';
          if not found then
            raise exception 'asset lesson is unavailable' using errcode='28000';
          end if;
          select coalesce(jsonb_agg(jsonb_build_object(
            'asset_id',draft.asset_id,'asset_version',draft.asset_version,
            'title',asset.title,'file_name',blob.file_name,'media_type',blob.media_type,
            'byte_size',blob.byte_size,'sha256',blob.sha256,'position',draft.position,
            'status',draft.status,'content_role',draft.content_role,
            'download_allowed',draft.download_allowed
          ) order by draft.position,draft.asset_id),'[]'::jsonb) into result
          from lesson_asset_draft draft
          join asset_record asset on asset.seller_organization_id=draft.seller_organization_id
            and asset.id=draft.asset_id
          join asset_blob blob on blob.seller_organization_id=draft.seller_organization_id
            and blob.id=draft.blob_id
          where draft.seller_organization_id=p_seller and draft.catalog_product_id=p_product
            and draft.course_id=p_course and draft.module_id=p_module
            and draft.lesson_id=p_lesson;
          return result;
        end $$
        """,
        "revoke all on function app_private.list_lesson_asset_drafts_v2(uuid,uuid,uuid,uuid,uuid,uuid) from public",
        "grant execute on function app_private.list_lesson_asset_drafts_v2(uuid,uuid,uuid,uuid,uuid,uuid) to appsell_runtime",
        "alter table publication drop constraint publication_schema_version_check",
        "alter table publication add constraint publication_schema_version_check check(schema_version in (1,2,3,4))",
        """
        create or replace function app_private.curriculum_courses_snapshot(
          p_seller uuid,p_product uuid
        ) returns jsonb language sql stable security definer
        set search_path=pg_catalog,public as $$
          select coalesce(jsonb_agg(jsonb_build_object(
            'id',course.id,'title',course.title,'description',course.description,
            'position',linked.position,
            'modules',coalesce((
              select jsonb_agg(jsonb_build_object(
                'id',module.id,'title',module.title,'status','active','position',module.position,
                'lessons',coalesce((
                  select jsonb_agg(jsonb_build_object(
                    'id',lesson.id,'title',lesson.title,'kind','text',
                    'content',lesson.body_text,'status','active','position',lesson.position,
                    'assets',coalesce((
                      select jsonb_agg(jsonb_build_object(
                        'id',draft.asset_id,'version',draft.asset_version,
                        'title',asset.title,'file_name',blob.file_name,
                        'media_type',blob.media_type,'byte_size',blob.byte_size,
                        'sha256',blob.sha256,'position',draft.position,
                        'content_role',draft.content_role,
                        'download_allowed',draft.download_allowed
                      ) order by draft.position,draft.asset_id)
                      from lesson_asset_draft draft
                      join asset_record asset
                        on asset.seller_organization_id=draft.seller_organization_id
                       and asset.id=draft.asset_id and asset.status='active'
                      join asset_blob blob
                        on blob.seller_organization_id=draft.seller_organization_id
                       and blob.id=draft.blob_id
                      where draft.seller_organization_id=p_seller
                        and draft.catalog_product_id=p_product
                        and draft.course_id=course.id and draft.module_id=module.id
                        and draft.lesson_id=lesson.id and draft.status='active'
                    ),'[]'::jsonb)
                  ) order by lesson.position,lesson.id)
                  from lesson where lesson.seller_organization_id=p_seller
                    and lesson.course_id=course.id and lesson.module_id=module.id
                    and lesson.status='active'
                ),'[]'::jsonb)
              ) order by module.position,module.id)
              from course_module module where module.seller_organization_id=p_seller
                and module.course_id=course.id and module.status='active'
            ),'[]'::jsonb)
          ) order by linked.position,course.id),'[]'::jsonb)
          from product_course linked join course
            on course.seller_organization_id=linked.seller_organization_id
           and course.id=linked.course_id
          where linked.seller_organization_id=p_seller
            and linked.catalog_product_id=p_product
        $$
        """,
        "revoke all on function app_private.curriculum_courses_snapshot(uuid,uuid) from public",
        """
        create or replace function app_private.project_publication(
          p_seller uuid,p_product uuid,p_publication uuid,p_snapshot jsonb
        ) returns void language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare course_item jsonb; module_item jsonb; lesson_item jsonb; asset_item jsonb;
          resolved record;
        begin
          if p_snapshot->>'schema_version' not in ('2','3','4') then return; end if;
          if p_snapshot->>'seller_organization_id'<>p_seller::text
             or p_snapshot->'product'->>'id'<>p_product::text
             or jsonb_typeof(p_snapshot->'courses')<>'array' then
            raise exception 'invalid publication projection' using errcode='22023';
          end if;
          for course_item in select value from jsonb_array_elements(p_snapshot->'courses') loop
            insert into publication_course(seller_organization_id,catalog_product_id,
              publication_id,course_id,position,lesson_count)
            values(p_seller,p_product,p_publication,(course_item->>'id')::uuid,
              (course_item->>'position')::integer,
              (select count(*) from jsonb_array_elements(course_item->'modules') module_value,
                jsonb_array_elements(module_value->'lessons') lesson_value));
            for module_item in select value from jsonb_array_elements(course_item->'modules') loop
              for lesson_item in select value from jsonb_array_elements(module_item->'lessons') loop
                insert into publication_lesson(seller_organization_id,catalog_product_id,
                  publication_id,course_id,module_id,lesson_id,course_position,
                  module_position,lesson_position)
                values(p_seller,p_product,p_publication,(course_item->>'id')::uuid,
                  (module_item->>'id')::uuid,(lesson_item->>'id')::uuid,
                  (course_item->>'position')::integer,(module_item->>'position')::integer,
                  (lesson_item->>'position')::integer);
                if p_snapshot->>'schema_version' in ('3','4') then
                  if jsonb_typeof(lesson_item->'assets')<>'array' then
                    raise exception 'invalid publication asset projection' using errcode='22023';
                  end if;
                  for asset_item in select value from jsonb_array_elements(lesson_item->'assets') loop
                    select draft.asset_id,draft.asset_version,draft.blob_id,draft.position,
                      draft.content_role,draft.download_allowed,asset.title,blob.file_name,
                      blob.media_type,blob.byte_size,blob.sha256,blob.storage_driver,
                      blob.storage_bucket,blob.object_key,blob.storage_version_id,blob.storage_etag
                    into resolved
                    from lesson_asset_draft draft
                    join asset_record asset
                      on asset.seller_organization_id=draft.seller_organization_id
                     and asset.id=draft.asset_id and asset.status='active'
                    join asset_blob blob
                      on blob.seller_organization_id=draft.seller_organization_id
                     and blob.id=draft.blob_id
                    where draft.seller_organization_id=p_seller
                      and draft.catalog_product_id=p_product
                      and draft.course_id=(course_item->>'id')::uuid
                      and draft.module_id=(module_item->>'id')::uuid
                      and draft.lesson_id=(lesson_item->>'id')::uuid
                      and draft.asset_id=(asset_item->>'id')::uuid
                      and draft.asset_version=(asset_item->>'version')::integer
                      and draft.status='active';
                    if not found or asset_item->>'title'<>resolved.title
                       or asset_item->>'file_name'<>resolved.file_name
                       or asset_item->>'media_type'<>resolved.media_type
                       or (asset_item->>'byte_size')::bigint<>resolved.byte_size
                       or asset_item->>'sha256'<>resolved.sha256
                       or (asset_item->>'position')::integer<>resolved.position
                       or (p_snapshot->>'schema_version'='4' and (
                         asset_item->>'content_role' is distinct from resolved.content_role
                         or (asset_item->>'download_allowed')::boolean
                           is distinct from resolved.download_allowed
                       )) then
                      raise exception 'publication asset changed during projection'
                        using errcode='40001';
                    end if;
                    insert into publication_asset(seller_organization_id,catalog_product_id,
                      publication_id,course_id,module_id,lesson_id,asset_id,asset_version,
                      blob_id,position,title,file_name,media_type,byte_size,sha256,
                      storage_driver,storage_bucket,object_key,storage_version_id,storage_etag,
                      content_role,download_allowed)
                    values(p_seller,p_product,p_publication,(course_item->>'id')::uuid,
                      (module_item->>'id')::uuid,(lesson_item->>'id')::uuid,
                      resolved.asset_id,resolved.asset_version,resolved.blob_id,resolved.position,
                      resolved.title,resolved.file_name,resolved.media_type,resolved.byte_size,
                      resolved.sha256,resolved.storage_driver,resolved.storage_bucket,
                      resolved.object_key,resolved.storage_version_id,resolved.storage_etag,
                      case when p_snapshot->>'schema_version'='4'
                        then resolved.content_role else 'course_content' end,
                      case when p_snapshot->>'schema_version'='4'
                        then resolved.download_allowed else false end);
                  end loop;
                end if;
              end loop;
            end loop;
          end loop;
        end $$
        """,
        "revoke all on function app_private.project_publication(uuid,uuid,uuid,jsonb) from public,appsell_runtime",
        r"""
        do $migration$
        declare definition text; changed text;
        begin
          definition:=pg_get_functiondef(
            'app_private.publish_product(uuid,uuid,uuid,uuid,bigint,bigint,jsonb,uuid,text,uuid)'
              ::regprocedure
          );
          changed:=regexp_replace(definition,
            $pattern$'schema_version'[[:space:]]*,[[:space:]]*3([[:space:]]*,[[:space:]]*'seller_organization_id')$pattern$,
            $replacement$'schema_version', 4\1$replacement$);
          changed:=regexp_replace(changed,
            $pattern$next_publication_version[[:space:]]*,[[:space:]]*3([[:space:]]*,[[:space:]]*generated_source_sha256)$pattern$,
            $replacement$next_publication_version, 4\1$replacement$);
          changed:=regexp_replace(changed,
            $pattern$'schema_version'[[:space:]]*,[[:space:]]*3([[:space:]]*\)[[:space:]]*\)[[:space:]]*;)$pattern$,
            $replacement$'schema_version', 4\1$replacement$);
          if changed=definition
             or changed~$pattern$'schema_version'[[:space:]]*,[[:space:]]*3$pattern$
             or changed~$pattern$next_publication_version[[:space:]]*,[[:space:]]*3[[:space:]]*,$pattern$
             or changed!~$pattern$'schema_version'[[:space:]]*,[[:space:]]*4$pattern$
             or changed!~$pattern$next_publication_version[[:space:]]*,[[:space:]]*4[[:space:]]*,$pattern$ then
            raise exception 'cannot safely promote publication contract to v4'
              using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """,
        """
        create function app_private.read_learning_player_v3(
          p_actor uuid,p_entitlement uuid,p_lesson uuid default null
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare player jsonb; assets jsonb;
        begin
          player:=app_private.read_learning_player_v2(p_actor,p_entitlement,p_lesson);
          select coalesce(jsonb_agg(jsonb_build_object(
            'id',asset.asset_id,'title',asset.title,'media_type',asset.media_type,
            'content_role',asset.content_role,
            'download_allowed',asset.download_allowed
          ) order by asset.position,asset.asset_id),'[]'::jsonb) into assets
          from publication_asset asset
          where asset.seller_organization_id=(player->'seller'->>'id')::uuid
            and asset.catalog_product_id=(player->'product'->>'id')::uuid
            and asset.publication_id=(player->'publication'->>'id')::uuid
            and asset.lesson_id=(player->>'selected_lesson_id')::uuid;
          return player||jsonb_build_object('schema_version',3,'selected_assets',assets);
        end $$
        """,
        "revoke all on function app_private.read_learning_player_v3(uuid,uuid,uuid) from public",
        "grant execute on function app_private.read_learning_player_v3(uuid,uuid,uuid) to appsell_runtime",
        """
        create function app_private.authorize_publication_asset_download(
          p_identity_user_id uuid,p_entitlement_id uuid,p_asset_id uuid
        ) returns table(asset_id uuid,asset_version integer,file_name text,title text,
          media_type text,byte_size bigint,sha256 text,storage_driver text,
          storage_bucket text,object_key text,storage_version_id text,storage_etag text)
        language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        begin
          if not app_private.library_context_allowed(
            p_identity_user_id,'library.asset.download'
          ) then
            raise exception 'asset download context is not authorized' using errcode='28000';
          end if;
          return query select asset.asset_id,asset.asset_version,asset.file_name,asset.title,
            asset.media_type,asset.byte_size,asset.sha256,asset.storage_driver,
            asset.storage_bucket,asset.object_key,asset.storage_version_id,asset.storage_etag
          from publication_asset asset
          join seller_organization organization on organization.id=asset.seller_organization_id
          join buyer_entitlement entitlement
            on entitlement.seller_organization_id=asset.seller_organization_id
           and entitlement.catalog_product_id=asset.catalog_product_id
           and entitlement.publication_id=asset.publication_id
           and entitlement.buyer_identity_user_id=p_identity_user_id
          join catalog_product product
            on product.seller_organization_id=entitlement.seller_organization_id
           and product.id=entitlement.catalog_product_id
          where entitlement.id=p_entitlement_id and asset.asset_id=p_asset_id
            and asset.content_role='support_material' and asset.download_allowed
            and asset.media_type='application/pdf'
            and entitlement.status='active' and entitlement.starts_at<=clock_timestamp()
            and (entitlement.expires_at is null or entitlement.expires_at>clock_timestamp())
            and organization.status='active' and product.status='published';
          if not found then
            raise exception 'downloadable learning asset is unavailable' using errcode='28000';
          end if;
        end $$
        """,
        "revoke all on function app_private.authorize_publication_asset_download(uuid,uuid,uuid) from public",
        "grant execute on function app_private.authorize_publication_asset_download(uuid,uuid,uuid) to appsell_runtime",
    ]
    for statement in statements:
        op.execute(statement)
    op.execute(_schema_ready_sql())


def downgrade() -> None:
    raise RuntimeError("0016_pdf_access_policy is forward-only; restore the prior release digest")
