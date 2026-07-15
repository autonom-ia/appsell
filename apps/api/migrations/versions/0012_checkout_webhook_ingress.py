"""Persist signed checkout webhooks and a durable transactional outbox.

Revision ID: 0012_checkout_webhook_ingress
Revises: 0011_public_storefront
"""

from alembic import op

revision: str = "0012_checkout_webhook_ingress"
down_revision: str | None = "0011_public_storefront"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    statements = [
        """
        create table checkout_integration (
          seller_organization_id uuid not null references seller_organization(id),
          id uuid not null default gen_random_uuid(),
          adapter text not null check (adapter='generic_hmac_v1'),
          display_name text not null check (
            display_name=btrim(display_name)
            and octet_length(display_name) between 2 and 120
            and display_name !~ '[[:cntrl:]]'
          ),
          status text not null default 'active' check (status in ('active','disabled')),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id,id)
        )
        """,
        """
        create table webhook_endpoint (
          seller_organization_id uuid not null,
          id uuid not null default gen_random_uuid(),
          checkout_integration_id uuid not null,
          endpoint_key_sha256 text not null unique check (
            endpoint_key_sha256 ~ '^[0-9a-f]{64}$'
          ),
          secret_reference text not null unique check (
            secret_reference ~ '^[a-z0-9][a-z0-9._-]{2,63}$'
          ),
          revision bigint not null default 1 check (revision > 0),
          max_clock_skew_seconds integer not null default 300 check (
            max_clock_skew_seconds between 30 and 900
          ),
          status text not null default 'active' check (status in ('active','disabled')),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id,id),
          unique (seller_organization_id,id,checkout_integration_id),
          foreign key (seller_organization_id,checkout_integration_id)
            references checkout_integration(seller_organization_id,id)
        )
        """,
        """
        create table external_product_mapping (
          seller_organization_id uuid not null,
          checkout_integration_id uuid not null,
          external_product_id_sha256 text not null check (
            external_product_id_sha256 ~ '^[0-9a-f]{64}$'
          ),
          external_product_id_octets smallint not null check (
            external_product_id_octets between 1 and 255
          ),
          catalog_product_id uuid not null,
          created_at timestamptz not null default clock_timestamp(),
          primary key (
            seller_organization_id,checkout_integration_id,external_product_id_sha256
          ),
          foreign key (seller_organization_id,checkout_integration_id)
            references checkout_integration(seller_organization_id,id),
          foreign key (seller_organization_id,catalog_product_id)
            references catalog_product(seller_organization_id,id)
        )
        """,
        """
        create table webhook_event (
          seller_organization_id uuid not null,
          id uuid not null default gen_random_uuid(),
          checkout_integration_id uuid not null,
          webhook_endpoint_id uuid not null,
          external_event_id_sha256 text not null check (
            external_event_id_sha256 ~ '^[0-9a-f]{64}$'
          ),
          external_event_id_octets smallint not null check (
            external_event_id_octets between 1 and 128
          ),
          payload_sha256 text not null check (payload_sha256 ~ '^[0-9a-f]{64}$'),
          payload_octets integer not null check (payload_octets between 0 and 65536),
          event_type text not null check (
            event_type in (
              'purchase.approved','purchase.refunded','subscription.cancelled'
            )
          ),
          external_product_id_sha256 text not null check (
            external_product_id_sha256 ~ '^[0-9a-f]{64}$'
          ),
          external_product_id_octets smallint not null check (
            external_product_id_octets between 1 and 255
          ),
          external_purchase_id_sha256 text not null check (
            external_purchase_id_sha256 ~ '^[0-9a-f]{64}$'
          ),
          external_purchase_id_octets smallint not null check (
            external_purchase_id_octets between 1 and 255
          ),
          occurred_at timestamptz not null,
          signed_at timestamptz not null,
          ingress_schema_version smallint not null default 1 check (
            ingress_schema_version=1
          ),
          received_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id,id),
          unique (seller_organization_id,webhook_endpoint_id,external_event_id_sha256),
          foreign key (
            seller_organization_id,webhook_endpoint_id,checkout_integration_id
          ) references webhook_endpoint(
            seller_organization_id,id,checkout_integration_id
          )
        )
        """,
        """
        create table app_private.outbox_event (
          id uuid primary key default gen_random_uuid(),
          seller_organization_id uuid not null,
          aggregate_type text not null check (aggregate_type='webhook_event'),
          aggregate_id uuid not null,
          event_type text not null check (event_type='commerce.webhook.received'),
          schema_version smallint not null default 1 check (schema_version=1),
          idempotency_key text not null unique check (
            idempotency_key ~ '^webhook:[0-9a-f-]{36}$'
          ),
          payload jsonb not null check (
            jsonb_typeof(payload)='object' and octet_length(payload::text) <= 4096
          ),
          status text not null default 'blocked_retention' check (
            status in ('blocked_retention','pending','completed')
          ),
          available_at timestamptz not null default clock_timestamp(),
          created_at timestamptz not null default clock_timestamp(),
          unique (seller_organization_id,aggregate_type,aggregate_id,event_type),
          foreign key (seller_organization_id,aggregate_id)
            references public.webhook_event(seller_organization_id,id)
        )
        """,
        """
        create index outbox_event_pending_idx
          on app_private.outbox_event(available_at,created_at,id)
          where status='pending'
        """,
        """
        create function app_private.reject_webhook_event_mutation()
        returns trigger language plpgsql set search_path=pg_catalog as $$
        begin
          raise exception 'webhook events are immutable' using errcode='55000';
        end $$
        """,
        """
        create trigger webhook_event_immutable
          before update or delete on webhook_event
          for each row execute function app_private.reject_webhook_event_mutation()
        """,
        "alter table checkout_integration enable row level security",
        "alter table checkout_integration force row level security",
        "alter table webhook_endpoint enable row level security",
        "alter table webhook_endpoint force row level security",
        "alter table external_product_mapping enable row level security",
        "alter table external_product_mapping force row level security",
        "alter table webhook_event enable row level security",
        "alter table webhook_event force row level security",
        """
        create policy checkout_integration_owner_all on checkout_integration
          to appsell_owner using (true) with check (true)
        """,
        """
        create policy webhook_endpoint_owner_all on webhook_endpoint
          to appsell_owner using (true) with check (true)
        """,
        """
        create policy external_product_mapping_owner_all on external_product_mapping
          to appsell_owner using (true) with check (true)
        """,
        """
        create policy webhook_event_owner_all on webhook_event
          to appsell_owner using (true) with check (true)
        """,
        """
        create function app_private.resolve_webhook_endpoint(p_endpoint_key_sha256 text)
        returns table(
          seller_organization_id uuid,webhook_endpoint_id uuid,
          checkout_integration_id uuid,adapter text,secret_reference text,
          endpoint_revision bigint,max_clock_skew_seconds integer
        ) language plpgsql stable security definer
        set search_path=pg_catalog,app_private,public as $$
        begin
          if app_private.context_text('capability') is distinct from 'commerce.webhook.ingest'
            or app_private.context_uuid('seller_organization_id') is not null
            or p_endpoint_key_sha256 is null
            or p_endpoint_key_sha256 !~ '^[0-9a-f]{64}$' then
            return;
          end if;
          return query
          select endpoint.seller_organization_id,endpoint.id,integration.id,
            integration.adapter,endpoint.secret_reference,endpoint.revision,
            endpoint.max_clock_skew_seconds
          from public.webhook_endpoint endpoint
          join public.checkout_integration integration
            on integration.seller_organization_id=endpoint.seller_organization_id
           and integration.id=endpoint.checkout_integration_id
          join public.seller_organization seller
            on seller.id=endpoint.seller_organization_id
          where endpoint.endpoint_key_sha256=p_endpoint_key_sha256
            and endpoint.status='active'
            and integration.status='active'
            and seller.status='active';
        end $$
        """,
        """
        create function app_private.accept_checkout_webhook(
          p_seller_organization_id uuid,p_endpoint_id uuid,
          p_endpoint_key_sha256 text,p_secret_reference text,p_endpoint_revision bigint,
          p_external_event_id_sha256 text,p_external_event_id_octets smallint,
          p_payload_sha256 text,p_payload_octets integer,p_event_type text,
          p_external_product_id_sha256 text,p_external_product_id_octets smallint,
          p_external_purchase_id_sha256 text,p_external_purchase_id_octets smallint,
          p_occurred_at timestamptz,p_signed_at_epoch bigint,p_request_id uuid
        ) returns table(webhook_event_id uuid,outbox_event_id uuid,created boolean)
        language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare
          endpoint record;
          previous public.webhook_event%rowtype;
          created_event_id uuid;
          created_outbox_id uuid;
          now_epoch bigint := extract(epoch from clock_timestamp())::bigint;
        begin
          if app_private.context_text('capability') is distinct from 'commerce.webhook.ingest'
            or app_private.context_uuid('seller_organization_id') is not null then
            raise exception 'webhook context is not authorized' using errcode='28000';
          end if;
          if p_seller_organization_id is null or p_endpoint_id is null
            or p_endpoint_key_sha256 is null
            or p_endpoint_key_sha256 !~ '^[0-9a-f]{64}$'
            or p_secret_reference is null
            or p_secret_reference !~ '^[a-z0-9][a-z0-9._-]{2,63}$'
            or p_endpoint_revision is null
            or p_external_event_id_sha256 is null
            or p_external_event_id_sha256 !~ '^[0-9a-f]{64}$'
            or p_external_event_id_octets is null
            or p_external_event_id_octets not between 1 and 128
            or p_payload_sha256 is null or p_payload_sha256 !~ '^[0-9a-f]{64}$'
            or p_payload_octets is null or p_payload_octets not between 0 and 65536
            or p_event_type is null or p_event_type not in (
              'purchase.approved','purchase.refunded','subscription.cancelled'
            )
            or p_external_product_id_sha256 is null
            or p_external_product_id_sha256 !~ '^[0-9a-f]{64}$'
            or p_external_product_id_octets is null
            or p_external_product_id_octets not between 1 and 255
            or p_external_purchase_id_sha256 is null
            or p_external_purchase_id_sha256 !~ '^[0-9a-f]{64}$'
            or p_external_purchase_id_octets is null
            or p_external_purchase_id_octets not between 1 and 255
            or p_occurred_at is null or p_signed_at_epoch is null
            or p_request_id is null then
            raise exception 'invalid webhook event' using errcode='22023';
          end if;

          select endpoint_row.seller_organization_id,endpoint_row.id,
            endpoint_row.checkout_integration_id,endpoint_row.revision,
            endpoint_row.max_clock_skew_seconds into endpoint
          from public.webhook_endpoint endpoint_row
          join public.checkout_integration integration
            on integration.seller_organization_id=endpoint_row.seller_organization_id
           and integration.id=endpoint_row.checkout_integration_id
          join public.seller_organization seller
            on seller.id=endpoint_row.seller_organization_id
          where endpoint_row.seller_organization_id=p_seller_organization_id
            and endpoint_row.id=p_endpoint_id
            and endpoint_row.endpoint_key_sha256=p_endpoint_key_sha256
            and endpoint_row.secret_reference=p_secret_reference
            and endpoint_row.status='active'
            and endpoint_row.revision=p_endpoint_revision
            and integration.status='active' and seller.status='active'
          for share of endpoint_row,integration,seller;
          if not found or abs(now_epoch-p_signed_at_epoch)>endpoint.max_clock_skew_seconds then
            raise exception 'webhook endpoint is unavailable' using errcode='28000';
          end if;

          perform pg_advisory_xact_lock(hashtextextended(
            endpoint.seller_organization_id::text || ':' || endpoint.id::text ||
              ':' || p_external_event_id_sha256,0
          ));
          select * into previous from public.webhook_event event
          where event.seller_organization_id=endpoint.seller_organization_id
            and event.webhook_endpoint_id=endpoint.id
            and event.external_event_id_sha256=p_external_event_id_sha256;
          if found then
            if previous.payload_sha256<>p_payload_sha256 then
              raise exception 'webhook event id conflict' using errcode='23505';
            end if;
            select outbox.id into strict created_outbox_id
            from app_private.outbox_event outbox
            where outbox.seller_organization_id=previous.seller_organization_id
              and outbox.aggregate_type='webhook_event'
              and outbox.aggregate_id=previous.id
              and outbox.event_type='commerce.webhook.received';
            return query select previous.id,created_outbox_id,false;
            return;
          end if;

          insert into public.webhook_event(
            seller_organization_id,checkout_integration_id,webhook_endpoint_id,
            external_event_id_sha256,external_event_id_octets,payload_sha256,
            payload_octets,event_type,external_product_id_sha256,
            external_product_id_octets,external_purchase_id_sha256,
            external_purchase_id_octets,occurred_at,signed_at
          ) values(
            endpoint.seller_organization_id,endpoint.checkout_integration_id,endpoint.id,
            p_external_event_id_sha256,p_external_event_id_octets,p_payload_sha256,
            p_payload_octets,p_event_type,p_external_product_id_sha256,
            p_external_product_id_octets,p_external_purchase_id_sha256,
            p_external_purchase_id_octets,p_occurred_at,to_timestamp(p_signed_at_epoch)
          ) returning id into created_event_id;
          insert into app_private.outbox_event(
            seller_organization_id,aggregate_type,aggregate_id,event_type,
            idempotency_key,payload
          ) values(
            endpoint.seller_organization_id,'webhook_event',created_event_id,
            'commerce.webhook.received','webhook:' || created_event_id::text,
            jsonb_build_object(
              'schema_version',1,'webhook_event_id',created_event_id,
              'seller_organization_id',endpoint.seller_organization_id,
              'checkout_integration_id',endpoint.checkout_integration_id,
              'webhook_endpoint_id',endpoint.id,'event_type',p_event_type,
              'payload_sha256',p_payload_sha256
            )
          ) returning id into created_outbox_id;
          insert into public.audit_event(
            seller_organization_id,actor_identity_user_id,action,target_type,
            target_id,result,request_id,metadata
          ) values(
            endpoint.seller_organization_id,null,'commerce.webhook_received',
            'webhook_event',created_event_id,'succeeded',p_request_id,
            jsonb_build_object(
              'adapter','generic_hmac_v1','webhook_endpoint_id',endpoint.id,
              'external_event_id_sha256',p_external_event_id_sha256,
              'payload_sha256',p_payload_sha256
            )
          );
          return query select created_event_id,created_outbox_id,true;
        end $$
        """,
        "revoke all on table checkout_integration,webhook_endpoint,external_product_mapping,webhook_event from public,appsell_runtime",
        "revoke all on table app_private.outbox_event from public,appsell_runtime",
        "revoke all on function app_private.reject_webhook_event_mutation() from public,appsell_runtime",
        "revoke all on function app_private.resolve_webhook_endpoint(text) from public",
        "revoke all on function app_private.accept_checkout_webhook(uuid,uuid,text,text,bigint,text,smallint,text,integer,text,text,smallint,text,smallint,timestamptz,bigint,uuid) from public",
        "grant execute on function app_private.resolve_webhook_endpoint(text) to appsell_runtime",
        "grant execute on function app_private.accept_checkout_webhook(uuid,uuid,text,text,bigint,text,smallint,text,integer,text,text,smallint,text,smallint,timestamptz,bigint,uuid) to appsell_runtime",
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
            and to_regprocedure('app_private.start_demo_inspection(uuid,text,text,integer,text,uuid)') is not null
            and to_regprocedure('app_private.read_demo_inspection(uuid,text,uuid,text,uuid)') is not null
            and to_regprocedure('app_private.revoke_demo_inspection(uuid,text,uuid,text,text,uuid)') is not null
            and to_regprocedure('app_private.read_public_storefront(text)') is not null
            and to_regprocedure('app_private.resolve_webhook_endpoint(text)') is not null
            and to_regprocedure('app_private.accept_checkout_webhook(uuid,uuid,text,text,bigint,text,smallint,text,integer,text,text,smallint,text,smallint,timestamptz,bigint,uuid)') is not null
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
            and exists(select 1 from public.alembic_version
              where version_num='0012_checkout_webhook_ingress')
            and exists(select 1 from app_private.context_signing_key where singleton)
            and exists(select 1 from app_private.bootstrap_trust_policy where singleton)
        $$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("AppSell migrations are forward-only")
