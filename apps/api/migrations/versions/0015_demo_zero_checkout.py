"""Add a fail-closed zero-value checkout for the isolated demo tenant.

Revision ID: 0015_demo_zero_checkout
Revises: 0014_multi_asset_player
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015_demo_zero_checkout"
down_revision: str | None = "0014_multi_asset_player"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema_ready_sql(expected_revision: str, *, include_checkout: bool) -> str:
    checkout_contract = ""
    if include_checkout:
        checkout_contract = """
            and to_regclass('public.demo_checkout_offer') is not null
            and to_regclass('public.demo_coupon') is not null
            and to_regclass('public.demo_order') is not null
            and to_regclass('public.demo_purchase_event') is not null
            and to_regclass('public.demo_purchase') is not null
            and to_regclass('app_private.demo_checkout_request') is not null
            and to_regclass('app_private.demo_checkout_outbox') is not null
            and to_regclass('app_private.demo_checkout_worker_identity') is not null
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
                'public.demo_checkout_offer'::regclass,
                'public.demo_coupon'::regclass,
                'public.demo_order'::regclass,
                'public.demo_purchase_event'::regclass,
                'public.demo_purchase'::regclass
              ]) relation(oid)
              join pg_catalog.pg_class class on class.oid=relation.oid
              where not class.relrowsecurity or not class.relforcerowsecurity
            )
    """
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
            and exists(select 1 from pg_catalog.pg_trigger
              where tgrelid='public.publication_asset'::regclass
                and tgname='publication_lesson_asset_limit' and not tgisinternal
                and tgenabled='O')
            and exists(select 1 from pg_catalog.pg_trigger
              where tgrelid='public.webhook_event'::regclass
                and tgname='webhook_event_immutable' and not tgisinternal
                and tgenabled='O')
            and not exists(
              select 1 from unnest(array[
                'public.checkout_integration'::regclass,
                'public.webhook_endpoint'::regclass,
                'public.external_product_mapping'::regclass,
                'public.webhook_event'::regclass
              ]) relation(oid)
              join pg_catalog.pg_class class on class.oid=relation.oid
              where not class.relrowsecurity or not class.relforcerowsecurity
            )
            {checkout_contract}
            and exists(select 1 from public.alembic_version
              where version_num='{expected_revision}')
            and exists(select 1 from app_private.context_signing_key where singleton)
            and exists(select 1 from app_private.bootstrap_trust_policy where singleton)
        $$
    """  # noqa: S608


def upgrade() -> None:
    statements = [
        """
        create table demo_checkout_offer (
          seller_organization_id uuid not null references seller_organization(id),
          offer_id uuid not null,
          catalog_product_id uuid not null,
          publication_id uuid not null,
          entitlement_id uuid not null,
          unit_amount_minor integer not null check(unit_amount_minor between 1 and 100000000),
          currency text not null check(currency ~ '^[A-Z]{3}$'),
          status text not null default 'active' check(status in ('active','disabled')),
          created_at timestamptz not null default clock_timestamp(),
          primary key(seller_organization_id,offer_id),
          unique(seller_organization_id,catalog_product_id,publication_id),
          unique(seller_organization_id,entitlement_id),
          foreign key(seller_organization_id,catalog_product_id,offer_id)
            references offer(seller_organization_id,catalog_product_id,id),
          foreign key(seller_organization_id,catalog_product_id,publication_id)
            references publication(seller_organization_id,catalog_product_id,id)
        )
        """,
        """
        create table demo_coupon (
          seller_organization_id uuid not null references seller_organization(id),
          id uuid not null,
          code_sha256 text not null check(code_sha256 ~ '^[0-9a-f]{64}$'),
          discount_bps integer not null check(discount_bps=10000),
          status text not null default 'active' check(status in ('active','disabled')),
          created_at timestamptz not null default clock_timestamp(),
          primary key(seller_organization_id,id),
          unique(seller_organization_id,code_sha256)
        )
        """,
        """
        create table demo_order (
          seller_organization_id uuid not null references seller_organization(id),
          id uuid not null default gen_random_uuid(),
          buyer_identity_user_id uuid not null references identity_user(id),
          offer_id uuid not null,
          catalog_product_id uuid not null,
          publication_id uuid not null,
          coupon_id uuid not null,
          subtotal_amount_minor integer not null check(subtotal_amount_minor>0),
          discount_amount_minor integer not null check(discount_amount_minor=subtotal_amount_minor),
          total_amount_minor integer not null check(total_amount_minor=0),
          currency text not null check(currency ~ '^[A-Z]{3}$'),
          status text not null check(status='approved'),
          payment_capture_required boolean not null default false check(not payment_capture_required),
          created_at timestamptz not null default clock_timestamp(),
          primary key(seller_organization_id,id),
          unique(seller_organization_id,buyer_identity_user_id,catalog_product_id),
          foreign key(seller_organization_id,offer_id)
            references demo_checkout_offer(seller_organization_id,offer_id),
          foreign key(seller_organization_id,coupon_id)
            references demo_coupon(seller_organization_id,id),
          foreign key(seller_organization_id,catalog_product_id,publication_id)
            references publication(seller_organization_id,catalog_product_id,id)
        )
        """,
        """
        create table demo_purchase_event (
          seller_organization_id uuid not null,
          id uuid not null default gen_random_uuid(),
          order_id uuid not null,
          event_type text not null check(event_type='purchase.approved'),
          event_key text not null check(
            event_key='demo-order:'||order_id::text||chr(58)||'approved'
          ),
          occurred_at timestamptz not null default clock_timestamp(),
          primary key(seller_organization_id,id),
          unique(seller_organization_id,id,order_id),
          unique(seller_organization_id,order_id,event_type),
          unique(seller_organization_id,event_key),
          foreign key(seller_organization_id,order_id)
            references demo_order(seller_organization_id,id)
        )
        """,
        """
        create table demo_purchase (
          seller_organization_id uuid not null,
          id uuid not null default gen_random_uuid(),
          order_id uuid not null,
          event_id uuid not null,
          buyer_identity_user_id uuid not null references identity_user(id),
          catalog_product_id uuid not null,
          publication_id uuid not null,
          status text not null check(status='approved'),
          created_at timestamptz not null default clock_timestamp(),
          primary key(seller_organization_id,id),
          unique(seller_organization_id,order_id),
          unique(seller_organization_id,event_id),
          unique(seller_organization_id,buyer_identity_user_id,catalog_product_id),
          unique(seller_organization_id,id,buyer_identity_user_id,catalog_product_id,publication_id),
          foreign key(seller_organization_id,order_id)
            references demo_order(seller_organization_id,id),
          foreign key(seller_organization_id,event_id)
            references demo_purchase_event(seller_organization_id,id),
          foreign key(seller_organization_id,event_id,order_id)
            references demo_purchase_event(seller_organization_id,id,order_id),
          foreign key(seller_organization_id,catalog_product_id,publication_id)
            references publication(seller_organization_id,catalog_product_id,id)
        )
        """,
        """
        create table app_private.demo_checkout_request (
          buyer_identity_user_id uuid not null references public.identity_user(id),
          idempotency_key uuid not null,
          body_sha256 text not null check(body_sha256 ~ '^[0-9a-f]{64}$'),
          seller_organization_id uuid not null,
          order_id uuid not null,
          purchase_id uuid,
          entitlement_id uuid,
          result jsonb not null check(jsonb_typeof(result)='object'),
          created_at timestamptz not null default clock_timestamp(),
          primary key(buyer_identity_user_id,idempotency_key),
          foreign key(seller_organization_id,order_id)
            references public.demo_order(seller_organization_id,id),
          foreign key(seller_organization_id,purchase_id)
            references public.demo_purchase(seller_organization_id,id),
          foreign key(seller_organization_id,entitlement_id)
            references public.buyer_entitlement(seller_organization_id,id)
        )
        """,
        """
        create table app_private.demo_checkout_outbox (
          seller_organization_id uuid not null,
          id uuid not null default gen_random_uuid(),
          order_id uuid not null,
          event_id uuid not null,
          event_type text not null check(event_type='commerce.demo_order.approved'),
          schema_version smallint not null check(schema_version=1),
          status text not null default 'pending'
            check(status in ('pending','leased','retry','completed','dead_letter')),
          attempt_count integer not null default 0 check(attempt_count between 0 and 20),
          available_at timestamptz not null default clock_timestamp(),
          lease_owner text,
          lease_fence bigint not null default 0 check(lease_fence>=0),
          lease_expires_at timestamptz,
          error_code text check(error_code in (
            'entitlement_conflict','apply_retryable','apply_unexpected'
          )),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key(seller_organization_id,id),
          unique(seller_organization_id,order_id,event_type),
          unique(seller_organization_id,event_id),
          foreign key(seller_organization_id,order_id)
            references public.demo_order(seller_organization_id,id),
          foreign key(seller_organization_id,event_id)
            references public.demo_purchase_event(seller_organization_id,id),
          foreign key(seller_organization_id,event_id,order_id)
            references public.demo_purchase_event(seller_organization_id,id,order_id),
          check((status='leased')=(lease_owner is not null and lease_expires_at is not null))
        )
        """,
        """
        create index demo_checkout_outbox_claim_idx
          on app_private.demo_checkout_outbox(status,available_at,created_at,id)
          where status in ('pending','retry','leased')
        """,
        """
        create table app_private.demo_checkout_worker_identity (
          identity_user_id uuid primary key references public.identity_user(id),
          status text not null check(status in ('active','disabled')),
          created_at timestamptz not null default clock_timestamp()
        )
        """,
        "alter table buyer_entitlement drop constraint buyer_entitlement_source_check",
        "alter table buyer_entitlement add column commerce_purchase_id uuid",
        "alter table buyer_entitlement add constraint buyer_entitlement_source_check check(source in ('demo_seed','manual','billing','demo_checkout'))",
        """
        alter table buyer_entitlement add constraint buyer_entitlement_demo_purchase_fkey
          foreign key(seller_organization_id,commerce_purchase_id,buyer_identity_user_id,
            catalog_product_id,publication_id)
          references demo_purchase(seller_organization_id,id,buyer_identity_user_id,
            catalog_product_id,publication_id)
        """,
        """
        alter table buyer_entitlement add constraint buyer_entitlement_demo_purchase_source_check
          check((source='demo_checkout')=(commerce_purchase_id is not null))
        """,
        """
        create unique index buyer_entitlement_demo_purchase_key
          on buyer_entitlement(seller_organization_id,commerce_purchase_id)
          where commerce_purchase_id is not null
        """,
        *[
            f"alter table {table} enable row level security"
            for table in (
                "demo_checkout_offer",
                "demo_coupon",
                "demo_order",
                "demo_purchase_event",
                "demo_purchase",
            )
        ],
        *[
            f"alter table {table} force row level security"
            for table in (
                "demo_checkout_offer",
                "demo_coupon",
                "demo_order",
                "demo_purchase_event",
                "demo_purchase",
            )
        ],
        *[
            f"create policy {table}_owner_all on {table} to appsell_owner using(true) with check(true)"
            for table in (
                "demo_checkout_offer",
                "demo_coupon",
                "demo_order",
                "demo_purchase_event",
                "demo_purchase",
            )
        ],
        """
        create function app_private.reject_demo_commerce_mutation() returns trigger
        language plpgsql security definer set search_path=pg_catalog as $$
          begin
          if current_setting('appsell.demo_reset_key',true)='autonomia-demo-v1' then
            return old;
          end if;
          raise exception 'demo commerce records are immutable' using errcode='55000';
        end $$
        """,
        "create trigger demo_order_immutable before update or delete on demo_order for each row execute function app_private.reject_demo_commerce_mutation()",
        "create trigger demo_purchase_event_immutable before update or delete on demo_purchase_event for each row execute function app_private.reject_demo_commerce_mutation()",
        "create trigger demo_purchase_immutable before update or delete on demo_purchase for each row execute function app_private.reject_demo_commerce_mutation()",
        """
        create function app_private.read_demo_checkout(p_actor uuid) returns jsonb
        language plpgsql stable security definer
        set search_path=pg_catalog,app_private,public as $$
        declare selected record; existing record;
        begin
          if app_private.context_text('capability') is distinct from 'commerce.demo.checkout.read'
             or app_private.context_uuid('identity_user_id') is distinct from p_actor
             or app_private.context_uuid('seller_organization_id') is not null then
            raise exception 'demo checkout context is not authorized' using errcode='28000';
          end if;
          select registry.seller_organization_id,registry.buyer_identity_user_id,
            config.offer_id,config.catalog_product_id,config.publication_id,
            config.unit_amount_minor,config.currency,product.slug product_slug,
            product.title product_title,seller.slug seller_slug,seller.display_name seller_name
          into selected
          from app_private.demo_tenant_registry registry
          join demo_checkout_offer config
            on config.seller_organization_id=registry.seller_organization_id
           and config.offer_id=registry.offer_id and config.status='active'
          join seller_organization seller on seller.id=registry.seller_organization_id
          join catalog_product product
            on product.seller_organization_id=config.seller_organization_id
           and product.id=config.catalog_product_id
          join identity_user buyer on buyer.id=registry.buyer_identity_user_id
          where registry.seed_key='autonomia-demo-v1'
            and registry.buyer_identity_user_id=p_actor
            and seller.status='active' and product.status='published'
            and product.published_snapshot_id=config.publication_id
            and buyer.email_verified;
          if not found then
            raise exception 'demo checkout is unavailable' using errcode='28000';
          end if;
          select purchase.id purchase_id,purchase.order_id,request.entitlement_id
          into existing from demo_purchase purchase
          left join app_private.demo_checkout_request request
            on request.seller_organization_id=purchase.seller_organization_id
           and request.purchase_id=purchase.id
          where purchase.seller_organization_id=selected.seller_organization_id
            and purchase.buyer_identity_user_id=p_actor
            and purchase.catalog_product_id=selected.catalog_product_id
          order by request.created_at nulls last limit 1;
          return jsonb_build_object(
            'schema_version',1,
            'seller',jsonb_build_object('id',selected.seller_organization_id,
              'slug',selected.seller_slug,'name',selected.seller_name),
            'product',jsonb_build_object('id',selected.catalog_product_id,
              'slug',selected.product_slug,'title',selected.product_title),
            'offer',jsonb_build_object('id',selected.offer_id,
              'unit_amount_minor',selected.unit_amount_minor,'currency',selected.currency),
            'already_owned',existing.purchase_id is not null,
            'purchase_id',existing.purchase_id,'order_id',existing.order_id,
            'entitlement_id',existing.entitlement_id
          );
        end $$
        """,
        """
        create function app_private.complete_demo_zero_checkout(
          p_actor uuid,p_coupon_sha256 text,p_idempotency uuid,p_body_sha256 text,p_request uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare selected record; existing app_private.demo_checkout_request%rowtype;
          prior_order demo_order%rowtype; prior_purchase demo_purchase%rowtype;
          prior_entitlement uuid; order_id uuid; event_id uuid; outbox_id uuid; response jsonb;
        begin
          if app_private.context_text('capability') is distinct from 'commerce.demo.checkout.complete'
             or app_private.context_uuid('identity_user_id') is distinct from p_actor
             or app_private.context_uuid('seller_organization_id') is not null
             or p_actor is null or p_idempotency is null or p_request is null
             or p_coupon_sha256 is null or p_coupon_sha256!~'^[0-9a-f]{64}$'
             or p_body_sha256 is null or p_body_sha256!~'^[0-9a-f]{64}$' then
            raise exception 'invalid demo checkout request' using errcode='22023';
          end if;
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.demo.checkout:'||p_actor::text||':'||p_idempotency::text,0));
          select * into existing from app_private.demo_checkout_request request
          where request.buyer_identity_user_id=p_actor and request.idempotency_key=p_idempotency;
          if found then
            if existing.body_sha256=p_body_sha256 then
              return jsonb_set(existing.result,'{applied}','false'::jsonb,true);
            end if;
            raise exception 'demo checkout idempotency conflict' using errcode='23505';
          end if;
          select registry.seller_organization_id,registry.buyer_identity_user_id,
            config.offer_id,config.catalog_product_id,config.publication_id,
            config.entitlement_id,config.unit_amount_minor,config.currency,coupon.id coupon_id
          into selected
          from app_private.demo_tenant_registry registry
          join demo_checkout_offer config
            on config.seller_organization_id=registry.seller_organization_id
           and config.offer_id=registry.offer_id and config.status='active'
          join demo_coupon coupon
            on coupon.seller_organization_id=config.seller_organization_id
           and coupon.code_sha256=p_coupon_sha256 and coupon.discount_bps=10000
           and coupon.status='active'
          join seller_organization seller on seller.id=registry.seller_organization_id
          join catalog_product product
            on product.seller_organization_id=config.seller_organization_id
           and product.id=config.catalog_product_id
          join identity_user buyer on buyer.id=registry.buyer_identity_user_id
          where registry.seed_key='autonomia-demo-v1'
            and registry.buyer_identity_user_id=p_actor
            and seller.status='active' and product.status='published'
            and product.published_snapshot_id=config.publication_id and buyer.email_verified
          for share of config,coupon,seller,product,buyer;
          if not found then
            raise exception 'demo checkout is unavailable' using errcode='28000';
          end if;
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.demo.purchase:'||selected.seller_organization_id::text||':'||
              p_actor::text||':'||selected.catalog_product_id::text,0));
          select * into prior_purchase from demo_purchase purchase
          where purchase.seller_organization_id=selected.seller_organization_id
            and purchase.buyer_identity_user_id=p_actor
            and purchase.catalog_product_id=selected.catalog_product_id;
          if found then
            select entitlement.id into strict prior_entitlement from buyer_entitlement entitlement
            where entitlement.seller_organization_id=selected.seller_organization_id
              and entitlement.commerce_purchase_id=prior_purchase.id;
            response:=jsonb_build_object('schema_version',1,'order_id',prior_purchase.order_id,
              'purchase_id',prior_purchase.id,'entitlement_id',prior_entitlement,
              'subtotal_amount_minor',selected.unit_amount_minor,
              'discount_amount_minor',selected.unit_amount_minor,'total_amount_minor',0,
              'currency',selected.currency,'status','approved','provider_called',false,
              'already_owned',true,'applied',false);
            insert into app_private.demo_checkout_request(
              buyer_identity_user_id,idempotency_key,body_sha256,seller_organization_id,
              order_id,purchase_id,entitlement_id,result
            ) values(p_actor,p_idempotency,p_body_sha256,selected.seller_organization_id,
              prior_purchase.order_id,prior_purchase.id,prior_entitlement,response);
            return response;
          end if;
          select * into prior_order from demo_order placed
          where placed.seller_organization_id=selected.seller_organization_id
            and placed.buyer_identity_user_id=p_actor
            and placed.catalog_product_id=selected.catalog_product_id;
          if found then
            response:=jsonb_build_object('schema_version',1,'order_id',prior_order.id,
              'purchase_id',null,'entitlement_id',null,
              'subtotal_amount_minor',selected.unit_amount_minor,
              'discount_amount_minor',selected.unit_amount_minor,'total_amount_minor',0,
              'currency',selected.currency,'status','processing','provider_called',false,
              'already_owned',false,'applied',false);
            insert into app_private.demo_checkout_request(
              buyer_identity_user_id,idempotency_key,body_sha256,seller_organization_id,
              order_id,result
            ) values(p_actor,p_idempotency,p_body_sha256,selected.seller_organization_id,
              prior_order.id,response);
            return response;
          end if;
          order_id:=gen_random_uuid(); event_id:=gen_random_uuid(); outbox_id:=gen_random_uuid();
          insert into demo_order(
            seller_organization_id,id,buyer_identity_user_id,offer_id,catalog_product_id,
            publication_id,coupon_id,subtotal_amount_minor,discount_amount_minor,
            total_amount_minor,currency,status,payment_capture_required
          ) values(selected.seller_organization_id,order_id,p_actor,selected.offer_id,
            selected.catalog_product_id,selected.publication_id,selected.coupon_id,
            selected.unit_amount_minor,selected.unit_amount_minor,0,selected.currency,
            'approved',false);
          insert into demo_purchase_event(seller_organization_id,id,order_id,event_type,event_key)
          values(selected.seller_organization_id,event_id,order_id,'purchase.approved',
            'demo-order:'||order_id::text||chr(58)||'approved');
          insert into app_private.demo_checkout_outbox(
            seller_organization_id,id,order_id,event_id,event_type,schema_version
          ) values(selected.seller_organization_id,outbox_id,order_id,event_id,
            'commerce.demo_order.approved',1);
          response:=jsonb_build_object('schema_version',1,'order_id',order_id,
            'purchase_id',null,'entitlement_id',null,
            'subtotal_amount_minor',selected.unit_amount_minor,
            'discount_amount_minor',selected.unit_amount_minor,'total_amount_minor',0,
            'currency',selected.currency,'status','processing','provider_called',false,
            'already_owned',false,'applied',true);
          insert into app_private.demo_checkout_request(
            buyer_identity_user_id,idempotency_key,body_sha256,seller_organization_id,
            order_id,result
          ) values(p_actor,p_idempotency,p_body_sha256,selected.seller_organization_id,
            order_id,response);
          insert into audit_event(
            seller_organization_id,actor_identity_user_id,action,target_type,target_id,
            result,request_id,metadata
          ) values(selected.seller_organization_id,p_actor,'commerce.demo_order_approved',
            'demo_order',order_id,'succeeded',p_request,jsonb_build_object(
              'event_id',event_id,'outbox_id',outbox_id,'total_amount_minor',0,
              'provider_called',false,'coupon_sha256',p_coupon_sha256));
          return response;
        end $$
        """,
        """
        create function app_private.claim_demo_checkout_jobs(
          p_worker uuid,p_label text,p_limit integer,p_lease_seconds integer
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare jobs jsonb;
        begin
          if app_private.context_text('capability') is distinct from 'commerce.demo.checkout.worker'
             or app_private.context_uuid('identity_user_id') is distinct from p_worker
             or app_private.context_uuid('seller_organization_id') is not null
             or p_label is null or p_label!~'^[a-z0-9][a-z0-9._-]{2,63}$'
             or p_limit not between 1 and 8 or p_lease_seconds not between 5 and 300
             or not exists(select 1 from app_private.demo_checkout_worker_identity worker
               where worker.identity_user_id=p_worker and worker.status='active') then
            raise exception 'demo checkout worker is not authorized' using errcode='28000';
          end if;
          update app_private.demo_checkout_outbox queued set status='dead_letter',
            lease_owner=null,lease_expires_at=null,error_code='apply_unexpected',
            updated_at=clock_timestamp()
          where queued.status='leased' and queued.lease_expires_at<=clock_timestamp()
            and queued.attempt_count>=10;
          with candidates as (
            select queued.seller_organization_id,queued.id
            from app_private.demo_checkout_outbox queued
            where queued.attempt_count<10 and (
              (queued.status in ('pending','retry') and queued.available_at<=clock_timestamp())
              or (queued.status='leased' and queued.lease_expires_at<=clock_timestamp())
            )
            order by queued.available_at,queued.created_at,queued.id
            for update skip locked limit p_limit
          ), leased as (
            update app_private.demo_checkout_outbox queued set status='leased',
              attempt_count=queued.attempt_count+1,lease_owner=p_label,
              lease_fence=queued.lease_fence+1,
              lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
              error_code=null,updated_at=clock_timestamp()
            from candidates where queued.seller_organization_id=candidates.seller_organization_id
              and queued.id=candidates.id
            returning queued.seller_organization_id,queued.id,queued.order_id,
              queued.event_id,queued.lease_fence,queued.attempt_count
          ) select coalesce(jsonb_agg(to_jsonb(leased) order by leased.id),'[]'::jsonb)
            into jobs from leased;
          return jobs;
        end $$
        """,
        """
        create function app_private.apply_demo_checkout_entitlement(
          p_worker uuid,p_label text,p_seller uuid,p_job uuid,p_fence bigint,p_request uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare job record; placed demo_order%rowtype; config demo_checkout_offer%rowtype;
          v_purchase_id uuid; v_entitlement_id uuid; response jsonb; conflict_entitlement uuid;
        begin
          if app_private.context_text('capability') is distinct from 'commerce.demo.checkout.worker'
             or app_private.context_uuid('identity_user_id') is distinct from p_worker
             or app_private.context_uuid('seller_organization_id') is distinct from p_seller
             or p_label is null or p_job is null or p_fence is null or p_request is null
             or not exists(select 1 from app_private.demo_checkout_worker_identity worker
               where worker.identity_user_id=p_worker and worker.status='active') then
            raise exception 'demo checkout apply is not authorized' using errcode='28000';
          end if;
          select queued.* into job from app_private.demo_checkout_outbox queued
          where queued.seller_organization_id=p_seller and queued.id=p_job
            and queued.status='leased' and queued.lease_owner=p_label
            and queued.lease_fence=p_fence and queued.lease_expires_at>clock_timestamp()
          for update;
          if not found then
            raise exception 'demo checkout lease is unavailable' using errcode='40001';
          end if;
          select * into strict placed from demo_order placed_order
          where placed_order.seller_organization_id=p_seller and placed_order.id=job.order_id;
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.demo.purchase:'||p_seller::text||':'||placed.buyer_identity_user_id::text||':'||
              placed.catalog_product_id::text,0));
          select * into strict config from demo_checkout_offer configured
          where configured.seller_organization_id=p_seller and configured.offer_id=placed.offer_id;
          select entitlement.id into conflict_entitlement from buyer_entitlement entitlement
          where entitlement.seller_organization_id=p_seller
            and entitlement.buyer_identity_user_id=placed.buyer_identity_user_id
            and entitlement.catalog_product_id=placed.catalog_product_id;
          if found then
            update app_private.demo_checkout_outbox set status='dead_letter',
              lease_owner=null,lease_expires_at=null,error_code='entitlement_conflict',
              updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_job;
            insert into audit_event(seller_organization_id,actor_identity_user_id,action,
              target_type,target_id,result,request_id,metadata)
            values(p_seller,p_worker,'commerce.demo_checkout_dead_letter','demo_order',
              placed.id,'failed',p_request,jsonb_build_object(
                'error_code','entitlement_conflict','entitlement_id',conflict_entitlement));
            return jsonb_build_object('status','dead_letter','error_code','entitlement_conflict');
          end if;
          v_purchase_id:=gen_random_uuid(); v_entitlement_id:=config.entitlement_id;
          insert into demo_purchase(seller_organization_id,id,order_id,event_id,
            buyer_identity_user_id,catalog_product_id,publication_id,status)
          values(p_seller,v_purchase_id,placed.id,job.event_id,placed.buyer_identity_user_id,
            placed.catalog_product_id,placed.publication_id,'approved');
          insert into buyer_entitlement(seller_organization_id,id,buyer_identity_user_id,
            catalog_product_id,publication_id,reference_key,source,status,progress_percent,
            commerce_purchase_id)
          values(p_seller,v_entitlement_id,placed.buyer_identity_user_id,
            placed.catalog_product_id,placed.publication_id,'demo-order:'||placed.id::text,
            'demo_checkout','active',0,v_purchase_id);
          response:=jsonb_build_object('schema_version',1,'order_id',placed.id,
            'purchase_id',v_purchase_id,'entitlement_id',v_entitlement_id,
            'subtotal_amount_minor',placed.subtotal_amount_minor,
            'discount_amount_minor',placed.discount_amount_minor,
            'total_amount_minor',0,'currency',placed.currency,'status','approved',
            'provider_called',false,'already_owned',false,'applied',true);
          update app_private.demo_checkout_request set purchase_id=v_purchase_id,
            entitlement_id=v_entitlement_id,result=response
          where seller_organization_id=p_seller and order_id=placed.id;
          update app_private.demo_checkout_outbox set status='completed',lease_owner=null,
            lease_expires_at=null,error_code=null,updated_at=clock_timestamp()
          where seller_organization_id=p_seller and id=p_job;
          insert into audit_event(seller_organization_id,actor_identity_user_id,action,
            target_type,target_id,result,request_id,metadata)
          values(p_seller,p_worker,'commerce.demo_purchase_approved','demo_purchase',
            v_purchase_id,'succeeded',p_request,jsonb_build_object(
              'order_id',placed.id,'event_id',job.event_id,'total_amount_minor',0,
              'provider_called',false));
          return response;
        end $$
        """,
        """
        create function app_private.fail_demo_checkout_job(
          p_worker uuid,p_label text,p_seller uuid,p_job uuid,p_fence bigint,
          p_error text,p_delay_seconds integer
        ) returns void language plpgsql security definer
        set search_path=pg_catalog,app_private as $$
        begin
          if app_private.context_text('capability') is distinct from 'commerce.demo.checkout.worker'
             or app_private.context_uuid('identity_user_id') is distinct from p_worker
             or app_private.context_uuid('seller_organization_id') is distinct from p_seller
             or p_error not in ('apply_retryable','apply_unexpected')
             or p_delay_seconds not between 1 and 300
             or not exists(select 1 from app_private.demo_checkout_worker_identity worker
               where worker.identity_user_id=p_worker and worker.status='active') then
            raise exception 'demo checkout failure record is not authorized' using errcode='28000';
          end if;
          update app_private.demo_checkout_outbox queued set
            status=case when queued.attempt_count>=10 then 'dead_letter' else 'retry' end,
            available_at=clock_timestamp()+make_interval(secs=>p_delay_seconds),
            lease_owner=null,lease_expires_at=null,error_code=p_error,
            updated_at=clock_timestamp()
          where queued.seller_organization_id=p_seller and queued.id=p_job
            and queued.status='leased' and queued.lease_owner=p_label
            and queued.lease_fence=p_fence and queued.lease_expires_at>clock_timestamp();
          if not found then
            raise exception 'demo checkout lease is unavailable' using errcode='40001';
          end if;
        end $$
        """,
        "revoke all on table demo_checkout_offer,demo_coupon,demo_order,demo_purchase_event,demo_purchase from public,appsell_runtime",
        "revoke all on table app_private.demo_checkout_request,app_private.demo_checkout_outbox,app_private.demo_checkout_worker_identity from public,appsell_runtime",
        "revoke all on function app_private.reject_demo_commerce_mutation() from public,appsell_runtime",
        "revoke all on function app_private.read_demo_checkout(uuid) from public",
        "revoke all on function app_private.complete_demo_zero_checkout(uuid,text,uuid,text,uuid) from public",
        "revoke all on function app_private.claim_demo_checkout_jobs(uuid,text,integer,integer) from public",
        "revoke all on function app_private.apply_demo_checkout_entitlement(uuid,text,uuid,uuid,bigint,uuid) from public",
        "revoke all on function app_private.fail_demo_checkout_job(uuid,text,uuid,uuid,bigint,text,integer) from public",
        "grant execute on function app_private.read_demo_checkout(uuid) to appsell_runtime",
        "grant execute on function app_private.complete_demo_zero_checkout(uuid,text,uuid,text,uuid) to appsell_runtime",
        "grant execute on function app_private.claim_demo_checkout_jobs(uuid,text,integer,integer) to appsell_runtime",
        "grant execute on function app_private.apply_demo_checkout_entitlement(uuid,text,uuid,uuid,bigint,uuid) to appsell_runtime",
        "grant execute on function app_private.fail_demo_checkout_job(uuid,text,uuid,uuid,bigint,text,integer) to appsell_runtime",
    ]
    for statement in statements:
        op.execute(statement)
    op.execute(_schema_ready_sql(revision, include_checkout=True))


def downgrade() -> None:
    raise RuntimeError(
        "0015_demo_zero_checkout is forward-only; disable the demo worker and restore the prior digest"
    )
