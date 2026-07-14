"""Create temporary, read-only superadmin inspection of the demo tenant.

Revision ID: 0010_release_candidate
Revises: 0009_buyer_multi_seller
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_release_candidate"
down_revision: str | None = "0009_buyer_multi_seller"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = [
        "lock table public.platform_grant,public.seller_membership in share row exclusive mode",
        """
        do $$ begin
          if exists(
            select 1 from public.platform_grant platform
            join public.seller_membership membership
              on membership.identity_user_id=platform.identity_user_id
            where platform.role='platform_superadmin' and platform.status='active'
              and membership.status='active'
          ) then
            raise exception '0010 rejects active superadmin seller memberships; revoke one side before upgrade';
          end if;
        end $$
        """,
        """
        create table app_private.demo_inspection_ticket (
          id uuid primary key default gen_random_uuid(),
          actor_identity_user_id uuid not null references public.identity_user(id),
          actor_identity_sub_sha256 text not null check (
            actor_identity_sub_sha256 ~ '^[0-9a-f]{64}$'
          ),
          seller_organization_id uuid not null references public.seller_organization(id),
          token_sha256 text not null unique check (token_sha256 ~ '^[0-9a-f]{64}$'),
          reason text not null check (
            reason = btrim(reason)
            and octet_length(reason) between 8 and 500
            and reason !~ '[[:cntrl:]]'
          ),
          state text not null default 'active' check (
            state in ('active','revoked','expired')
          ),
          started_at timestamptz not null default clock_timestamp(),
          expires_at timestamptz not null,
          revoked_at timestamptz,
          revoked_reason text check (
            revoked_reason is null or (
              revoked_reason = btrim(revoked_reason)
              and octet_length(revoked_reason) between 8 and 500
              and revoked_reason !~ '[[:cntrl:]]'
            )
          ),
          check (expires_at > started_at and expires_at <= started_at + interval '15 minutes'),
          check (
            (state = 'active' and revoked_at is null and revoked_reason is null)
            or (state = 'expired' and revoked_at is null and revoked_reason is null)
            or (state = 'revoked' and revoked_at is not null and revoked_reason is not null)
          )
        )
        """,
        """
        create unique index demo_inspection_one_active_actor_idx
          on app_private.demo_inspection_ticket(actor_identity_user_id)
          where state = 'active'
        """,
        """
        create index demo_inspection_actor_time_idx
          on app_private.demo_inspection_ticket(
            actor_identity_user_id,started_at desc,id
          )
        """,
        """
        create index demo_inspection_seller_time_idx
          on app_private.demo_inspection_ticket(
            seller_organization_id,started_at desc,id
          )
        """,
        "revoke all on table app_private.demo_inspection_ticket from public,appsell_runtime",
        """
        create function app_private.superadmin_demo_context_allowed(
          p_identity_user_id uuid,p_identity_sub text,p_capability text
        ) returns boolean
        language sql stable security definer
        set search_path=pg_catalog,app_private,public as $$
          select p_identity_user_id is not null
            and p_identity_sub is not null
            and p_identity_sub = btrim(p_identity_sub)
            and octet_length(p_identity_sub) between 3 and 255
            and p_identity_sub !~ '[[:cntrl:]]'
            and p_capability in (
              'platform.demo.inspect.start',
              'platform.demo.inspect.read',
              'platform.demo.inspect.revoke'
            )
            and app_private.context_uuid('identity_user_id') = p_identity_user_id
            and app_private.context_uuid('seller_organization_id') is null
            and app_private.context_text('capability') = p_capability
            and exists (
              select 1 from public.identity_user identity
              join public.platform_grant platform
                on platform.identity_user_id=identity.id
              where identity.id=p_identity_user_id
                and identity.identity_sub=p_identity_sub
                and platform.role='platform_superadmin'
                and platform.status='active'
            )
        $$
        """,
        """
        create function app_private.lock_active_superadmin(
          p_identity_user_id uuid,p_identity_sub text
        ) returns boolean
        language plpgsql security definer
        set search_path=pg_catalog,public as $$
        begin
          perform 1 from public.identity_user identity
          join public.platform_grant platform
            on platform.identity_user_id=identity.id
          where identity.id=p_identity_user_id
            and identity.identity_sub=p_identity_sub
            and platform.role='platform_superadmin'
            and platform.status='active'
          for share of identity,platform;
          return found;
        end $$
        """,
        """
        create function app_private.start_demo_inspection(
          p_identity_user_id uuid,p_identity_sub text,p_reason text,
          p_ttl_seconds integer,p_token_sha256 text,p_request_id uuid
        ) returns table(
          inspection_id uuid,expires_at timestamptz,
          seller_id uuid,seller_slug text,seller_display_name text
        )
        language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare
          demo record;
          previous app_private.demo_inspection_ticket%rowtype;
          created app_private.demo_inspection_ticket%rowtype;
        begin
          if not app_private.superadmin_demo_context_allowed(
            p_identity_user_id,p_identity_sub,'platform.demo.inspect.start'
          ) then
            raise exception 'demo inspection context is not authorized' using errcode='28000';
          end if;
          if p_reason is null or p_reason <> btrim(p_reason)
            or octet_length(p_reason) not between 8 and 500
            or p_reason ~ '[[:cntrl:]]'
            or p_ttl_seconds is null or p_ttl_seconds not between 30 and 900
            or p_token_sha256 is null or p_token_sha256 !~ '^[0-9a-f]{64}$'
            or p_request_id is null then
            raise exception 'invalid demo inspection request' using errcode='22023';
          end if;

          perform pg_advisory_xact_lock(
            hashtextextended('autonomia-demo-v1',0)
          );
          perform pg_advisory_xact_lock(
            hashtextextended('appsell.demo.inspection:' || p_identity_user_id::text,0)
          );
          if not app_private.lock_active_superadmin(
            p_identity_user_id,p_identity_sub
          ) then
            raise exception 'active superadmin grant is unavailable' using errcode='28000';
          end if;

          select registry.seller_organization_id,organization.slug,
            organization.display_name into strict demo
          from app_private.demo_tenant_registry registry
          join public.seller_organization organization
            on organization.id=registry.seller_organization_id
          where registry.seed_key='autonomia-demo-v1'
            and organization.status='active';

          select * into previous from app_private.demo_inspection_ticket ticket
          where ticket.actor_identity_user_id=p_identity_user_id
            and ticket.state='active' for update;
          if found then
            if previous.expires_at <= clock_timestamp() then
              update app_private.demo_inspection_ticket set state='expired'
                where id=previous.id;
              insert into public.audit_event(
                seller_organization_id,actor_identity_user_id,action,target_type,
                target_id,result,request_id,metadata
              ) values(
                previous.seller_organization_id,p_identity_user_id,
                'platform.demo_inspection.expired','demo_inspection',previous.id,
                'succeeded',p_request_id,jsonb_build_object('trigger','rotation')
              );
            else
              update app_private.demo_inspection_ticket set
                state='revoked',revoked_at=clock_timestamp(),
                revoked_reason='Rotated by a new inspection request'
                where id=previous.id;
              insert into public.audit_event(
                seller_organization_id,actor_identity_user_id,action,target_type,
                target_id,result,request_id,metadata
              ) values(
                previous.seller_organization_id,p_identity_user_id,
                'platform.demo_inspection.rotated','demo_inspection',previous.id,
                'succeeded',p_request_id,jsonb_build_object('trigger','new_inspection')
              );
            end if;
          end if;

          insert into app_private.demo_inspection_ticket(
            actor_identity_user_id,actor_identity_sub_sha256,
            seller_organization_id,token_sha256,reason,expires_at
          ) values(
            p_identity_user_id,
            encode(public.digest(convert_to(p_identity_sub,'UTF8'),'sha256'),'hex'),
            demo.seller_organization_id,p_token_sha256,p_reason,
            clock_timestamp()+make_interval(secs=>p_ttl_seconds)
          ) returning * into created;
          insert into public.audit_event(
            seller_organization_id,actor_identity_user_id,action,target_type,
            target_id,result,request_id,metadata
          ) values(
            created.seller_organization_id,p_identity_user_id,
            'platform.demo_inspection.started','demo_inspection',created.id,
            'succeeded',p_request_id,
            jsonb_build_object('reason',p_reason,'ttl_seconds',p_ttl_seconds)
          );
          return query select created.id,created.expires_at,
            demo.seller_organization_id,demo.slug,demo.display_name;
        exception when no_data_found then
          raise exception 'demo tenant is unavailable' using errcode='55000';
        end $$
        """,
        """
        create function app_private.read_demo_inspection(
          p_identity_user_id uuid,p_identity_sub text,p_inspection_id uuid,
          p_token_sha256 text,p_request_id uuid
        ) returns jsonb
        language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare
          ticket app_private.demo_inspection_ticket%rowtype;
          projection jsonb;
        begin
          if not app_private.superadmin_demo_context_allowed(
            p_identity_user_id,p_identity_sub,'platform.demo.inspect.read'
          ) then
            raise exception 'demo inspection context is not authorized' using errcode='28000';
          end if;
          if p_inspection_id is null or p_request_id is null
            or p_token_sha256 is null or p_token_sha256 !~ '^[0-9a-f]{64}$' then
            return null;
          end if;
          perform pg_advisory_xact_lock(
            hashtextextended('autonomia-demo-v1',0)
          );
          perform pg_advisory_xact_lock(
            hashtextextended('appsell.demo.inspection:' || p_identity_user_id::text,0)
          );
          if not app_private.lock_active_superadmin(
            p_identity_user_id,p_identity_sub
          ) then
            raise exception 'active superadmin grant is unavailable' using errcode='28000';
          end if;
          select * into ticket from app_private.demo_inspection_ticket inspection
          where inspection.id=p_inspection_id
            and inspection.actor_identity_user_id=p_identity_user_id
            and inspection.actor_identity_sub_sha256=encode(
              public.digest(convert_to(p_identity_sub,'UTF8'),'sha256'),'hex'
            )
            and inspection.token_sha256=p_token_sha256
          for update;
          if not found then return null; end if;
          if ticket.state='active' and ticket.expires_at <= clock_timestamp() then
            update app_private.demo_inspection_ticket set state='expired'
              where id=ticket.id;
            insert into public.audit_event(
              seller_organization_id,actor_identity_user_id,action,target_type,
              target_id,result,request_id,metadata
            ) values(
              ticket.seller_organization_id,p_identity_user_id,
              'platform.demo_inspection.expired','demo_inspection',ticket.id,
              'succeeded',p_request_id,jsonb_build_object('trigger','read')
            );
            return null;
          end if;
          if ticket.state <> 'active' then return null; end if;

          select jsonb_build_object(
            'inspection',jsonb_build_object(
              'id',ticket.id,'started_at',ticket.started_at,'expires_at',ticket.expires_at
            ),
            'seller',jsonb_build_object(
              'id',organization.id,'slug',organization.slug,
              'display_name',organization.display_name,'status',organization.status
            ),
            'product',jsonb_build_object(
              'id',product.id,'slug',product.slug,'title',product.title,'status',product.status
            ),
            'publication',jsonb_build_object(
              'id',publication.id,'version',publication.version,
              'schema_version',publication.schema_version,
              'published_at',publication.published_at
            ),
            'courses',coalesce((
              select jsonb_agg(jsonb_build_object(
                'id',course_item->>'id','title',course_item->>'title',
                'position',(course_item->>'position')::integer
              ) order by (course_item->>'position')::integer,course_item->>'id')
              from jsonb_array_elements(publication.snapshot->'courses') course_item
            ),'[]'::jsonb)
          ) into projection
          from app_private.demo_tenant_registry registry
          join public.seller_organization organization
            on organization.id=registry.seller_organization_id
          join public.catalog_product product
            on product.seller_organization_id=registry.seller_organization_id
           and product.id=registry.catalog_product_id
          join public.publication publication
            on publication.seller_organization_id=product.seller_organization_id
           and publication.catalog_product_id=product.id
           and publication.id=product.published_snapshot_id
          where registry.seed_key='autonomia-demo-v1'
            and registry.seller_organization_id=ticket.seller_organization_id
            and organization.status='active' and product.status='published';
          if projection is null then return null; end if;
          insert into public.audit_event(
            seller_organization_id,actor_identity_user_id,action,target_type,
            target_id,result,request_id,metadata
          ) values(
            ticket.seller_organization_id,p_identity_user_id,
            'platform.demo_inspection.read','demo_inspection',ticket.id,
            'allowed',p_request_id,jsonb_build_object('projection','published_demo_metadata')
          );
          return projection;
        end $$
        """,
        """
        create function app_private.revoke_demo_inspection(
          p_identity_user_id uuid,p_identity_sub text,p_inspection_id uuid,
          p_token_sha256 text,p_reason text,p_request_id uuid
        ) returns jsonb
        language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare ticket app_private.demo_inspection_ticket%rowtype;
        begin
          if not app_private.superadmin_demo_context_allowed(
            p_identity_user_id,p_identity_sub,'platform.demo.inspect.revoke'
          ) then
            raise exception 'demo inspection context is not authorized' using errcode='28000';
          end if;
          if p_inspection_id is null or p_request_id is null
            or p_token_sha256 is null or p_token_sha256 !~ '^[0-9a-f]{64}$'
            or p_reason is null or p_reason <> btrim(p_reason)
            or octet_length(p_reason) not between 8 and 500
            or p_reason ~ '[[:cntrl:]]' then
            return null;
          end if;
          perform pg_advisory_xact_lock(
            hashtextextended('autonomia-demo-v1',0)
          );
          perform pg_advisory_xact_lock(
            hashtextextended('appsell.demo.inspection:' || p_identity_user_id::text,0)
          );
          if not app_private.lock_active_superadmin(
            p_identity_user_id,p_identity_sub
          ) then
            raise exception 'active superadmin grant is unavailable' using errcode='28000';
          end if;
          select * into ticket from app_private.demo_inspection_ticket inspection
          where inspection.id=p_inspection_id
            and inspection.actor_identity_user_id=p_identity_user_id
            and inspection.actor_identity_sub_sha256=encode(
              public.digest(convert_to(p_identity_sub,'UTF8'),'sha256'),'hex'
            )
            and inspection.token_sha256=p_token_sha256
          for update;
          if not found then return null; end if;
          if ticket.state='active' and ticket.expires_at <= clock_timestamp() then
            update app_private.demo_inspection_ticket set state='expired'
              where id=ticket.id;
            insert into public.audit_event(
              seller_organization_id,actor_identity_user_id,action,target_type,
              target_id,result,request_id,metadata
            ) values(
              ticket.seller_organization_id,p_identity_user_id,
              'platform.demo_inspection.expired','demo_inspection',ticket.id,
              'succeeded',p_request_id,jsonb_build_object('trigger','revoke')
            );
            return null;
          end if;
          if ticket.state <> 'active' then return null; end if;
          update app_private.demo_inspection_ticket set
            state='revoked',revoked_at=clock_timestamp(),revoked_reason=p_reason
            where id=ticket.id returning * into ticket;
          insert into public.audit_event(
            seller_organization_id,actor_identity_user_id,action,target_type,
            target_id,result,request_id,metadata
          ) values(
            ticket.seller_organization_id,p_identity_user_id,
            'platform.demo_inspection.revoked','demo_inspection',ticket.id,
            'succeeded',p_request_id,jsonb_build_object('reason',p_reason)
          );
          return jsonb_build_object(
            'inspection_id',ticket.id,'status','revoked','revoked_at',ticket.revoked_at
          );
        end $$
        """,
        """
        create function app_private.reject_superadmin_membership()
        returns trigger language plpgsql security definer
        set search_path=pg_catalog,public as $$
        begin
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.platform-seller-exclusive:' || new.identity_user_id::text,0
          ));
          if new.status='active' and exists(
            select 1 from public.platform_grant platform
            where platform.identity_user_id=new.identity_user_id
              and platform.role='platform_superadmin' and platform.status='active'
          ) then
            raise exception 'platform superadmin cannot receive seller membership'
              using errcode='28000';
          end if;
          return new;
        end $$
        """,
        """
        create function app_private.reject_member_superadmin_grant()
        returns trigger language plpgsql security definer
        set search_path=pg_catalog,public as $$
        begin
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.platform-seller-exclusive:' || new.identity_user_id::text,0
          ));
          if new.role='platform_superadmin' and new.status='active' and exists(
            select 1 from public.seller_membership membership
            where membership.identity_user_id=new.identity_user_id
              and membership.status='active'
          ) then
            raise exception 'seller member cannot receive platform superadmin grant'
              using errcode='28000';
          end if;
          return new;
        end $$
        """,
        """
        create trigger seller_membership_reject_superadmin
          before insert or update of identity_user_id,status on public.seller_membership
          for each row execute function app_private.reject_superadmin_membership()
        """,
        """
        create trigger platform_grant_reject_seller_member
          before insert or update of identity_user_id,role,status on public.platform_grant
          for each row execute function app_private.reject_member_superadmin_grant()
        """,
        "revoke all on function app_private.superadmin_demo_context_allowed(uuid,text,text) from public",
        "revoke all on function app_private.lock_active_superadmin(uuid,text) from public",
        "revoke all on function app_private.start_demo_inspection(uuid,text,text,integer,text,uuid) from public",
        "revoke all on function app_private.read_demo_inspection(uuid,text,uuid,text,uuid) from public",
        "revoke all on function app_private.revoke_demo_inspection(uuid,text,uuid,text,text,uuid) from public",
        "revoke all on function app_private.reject_superadmin_membership() from public",
        "revoke all on function app_private.reject_member_superadmin_grant() from public",
        "grant execute on function app_private.start_demo_inspection(uuid,text,text,integer,text,uuid) to appsell_runtime",
        "grant execute on function app_private.read_demo_inspection(uuid,text,uuid,text,uuid) to appsell_runtime",
        "grant execute on function app_private.revoke_demo_inspection(uuid,text,uuid,text,text,uuid) to appsell_runtime",
        """
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
            and to_regprocedure('app_private.read_buyer_products(uuid)') is not null
            and to_regprocedure(
              'app_private.read_buyer_seller_storefront(uuid,uuid)'
            ) is not null
            and to_regprocedure(
              'app_private.start_demo_inspection(uuid,text,text,integer,text,uuid)'
            ) is not null
            and to_regprocedure(
              'app_private.read_demo_inspection(uuid,text,uuid,text,uuid)'
            ) is not null
            and to_regprocedure(
              'app_private.revoke_demo_inspection(uuid,text,uuid,text,text,uuid)'
            ) is not null
            and exists(select 1 from public.alembic_version
              where version_num='0010_release_candidate')
            and exists(select 1 from app_private.context_signing_key where singleton)
            and exists(select 1 from app_private.bootstrap_trust_policy where singleton)
        $$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("AppSell migrations are forward-only")
