"""Enable validated Offer status transitions.

Revision ID: 0019_offer_status
Revises: 0018_offer_pricing

Offers remain ``draft`` by default. The only forward transitions are
``draft -> active -> archived``; archived is terminal. Activation requires a
positive price. Downgrade restores the original draft-only constraint only when
there is no status or transition history that would need to be discarded.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019_offer_status"
down_revision: str | None = "0018_offer_pricing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = [
        """
        alter table offer
          drop constraint offer_status_check,
          add constraint offer_status_check
            check (status in ('draft', 'active', 'archived'))
        """,
        """
        alter table app_private.catalog_mutation_request
          drop constraint catalog_mutation_request_operation_check,
          add constraint catalog_mutation_request_operation_check
            check (operation in ('draft.update', 'product.publish', 'offer.status.update'))
        """,
        """
        create function app_private.transition_offer_status(
          p_identity_user_id uuid,
          p_seller_organization_id uuid,
          p_catalog_product_id uuid,
          p_offer_id uuid,
          p_expected_offer_status text,
          p_offer_status text,
          p_expected_offer_version bigint,
          p_idempotency_key uuid,
          p_body_sha256 text,
          p_request_id uuid
        ) returns table (
          offer_status text,
          offer_version bigint,
          changed boolean
        )
        language plpgsql security definer
        set search_path = pg_catalog, app_private, public
        as $$
        declare
          existing_request app_private.catalog_mutation_request%rowtype;
          locked_offer public.offer%rowtype;
          next_offer_version bigint;
          response jsonb;
        begin
          if app_private.context_text('capability') <> 'catalog.offer.status.update'
             or app_private.context_uuid('identity_user_id') is distinct from
                p_identity_user_id
             or app_private.context_uuid('seller_organization_id') is distinct from
                p_seller_organization_id then
            raise exception 'offer status context is not authorized'
              using errcode = '28000';
          end if;
          if p_identity_user_id is null or p_seller_organization_id is null
             or p_catalog_product_id is null or p_offer_id is null
             or p_expected_offer_status is null
             or p_expected_offer_status not in ('draft', 'active', 'archived')
             or p_offer_status is null
             or p_offer_status not in ('draft', 'active', 'archived')
             or not (
               (p_expected_offer_status = 'draft' and p_offer_status = 'active')
               or (p_expected_offer_status = 'active' and p_offer_status = 'archived')
             )
             or p_expected_offer_version is null or p_expected_offer_version < 1
             or p_idempotency_key is null or p_request_id is null
             or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$' then
            raise exception 'invalid offer status request' using errcode = '22023';
          end if;

          perform 1
          from public.seller_organization organization
          join public.seller_membership membership
            on membership.seller_organization_id = organization.id
          where organization.id = p_seller_organization_id
            and organization.status = 'active'
            and membership.identity_user_id = p_identity_user_id
            and membership.role = 'seller_owner'
            and membership.status = 'active'
          for key share of organization, membership;
          if not found then
            raise exception 'offer status owner is not authorized' using errcode = '28000';
          end if;

          perform pg_advisory_xact_lock(
            hashtextextended(
              'appsell.catalog.mutation:' || p_seller_organization_id::text ||
              '/offer.status.update/' || p_idempotency_key::text,
              0
            )
          );
          select * into existing_request
          from app_private.catalog_mutation_request request
          where request.seller_organization_id = p_seller_organization_id
            and request.operation = 'offer.status.update'
            and request.idempotency_key = p_idempotency_key;
          if found then
            if existing_request.identity_user_id = p_identity_user_id
               and existing_request.body_sha256 = p_body_sha256
               and existing_request.catalog_product_id = p_catalog_product_id
               and existing_request.offer_id = p_offer_id then
              return query select
                existing_request.result ->> 'offer_status',
                (existing_request.result ->> 'offer_version')::bigint,
                false;
              return;
            end if;
            raise exception 'offer status idempotency conflict' using errcode = '23505';
          end if;

          select * into locked_offer
          from public.offer candidate
          where candidate.seller_organization_id = p_seller_organization_id
            and candidate.catalog_product_id = p_catalog_product_id
            and candidate.id = p_offer_id
          for update;
          if not found then
            raise exception 'catalog offer is unavailable' using errcode = '28000';
          end if;
          if locked_offer.version <> p_expected_offer_version then
            raise exception 'catalog offer version conflict' using errcode = '40001';
          end if;
          if locked_offer.status <> p_expected_offer_status then
            raise exception 'catalog offer status conflict' using errcode = 'P4092';
          end if;
          if p_offer_status = 'active' and locked_offer.unit_amount_minor <= 0 then
            raise exception 'active offer requires a positive price' using errcode = 'P4222';
          end if;

          update public.offer
          set status = p_offer_status,
              version = version + 1,
              updated_at = clock_timestamp()
          where seller_organization_id = p_seller_organization_id
            and id = p_offer_id
          returning version into next_offer_version;

          response := jsonb_build_object(
            'offer_status', p_offer_status,
            'offer_version', next_offer_version
          );
          insert into app_private.catalog_mutation_request (
            seller_organization_id, operation, idempotency_key,
            identity_user_id, body_sha256, catalog_product_id, offer_id, result
          ) values (
            p_seller_organization_id, 'offer.status.update', p_idempotency_key,
            p_identity_user_id, p_body_sha256, p_catalog_product_id, p_offer_id, response
          );
          insert into public.audit_event (
            seller_organization_id, actor_identity_user_id, action, target_type,
            target_id, result, request_id, metadata
          ) values (
            p_seller_organization_id, p_identity_user_id,
            'catalog.offer_status_updated', 'offer', p_offer_id,
            'succeeded', p_request_id,
            jsonb_build_object(
              'catalog_product_id', p_catalog_product_id,
              'previous_status', locked_offer.status,
              'offer_status', p_offer_status,
              'offer_version', next_offer_version
            )
          );
          return query select p_offer_status, next_offer_version, true;
        end
        $$
        """,
        """
        revoke all on function app_private.transition_offer_status(
          uuid, uuid, uuid, uuid, text, text, bigint, uuid, text, uuid
        ) from public
        """,
        """
        grant execute on function app_private.transition_offer_status(
          uuid, uuid, uuid, uuid, text, text, bigint, uuid, text, uuid
        ) to appsell_runtime
        """,
        r"""
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0016()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing')$needle$,
            $replacement$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status')$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0016 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;

          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$version_num in ('0017_pdf_preraster','0018_offer_pricing')$needle$,
            $replacement$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status')$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0017 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        """
        create or replace function app_private.schema_ready() returns boolean
        language sql stable security definer set search_path=pg_catalog,app_private as $$
          select app_private.schema_ready_base_0017()
            and exists(select 1 from pg_catalog.pg_attribute
              where attrelid='public.offer'::regclass
                and attname='unit_amount_minor' and not attisdropped)
            and exists(select 1 from pg_catalog.pg_attribute
              where attrelid='public.offer'::regclass
                and attname='currency' and not attisdropped)
            and exists(select 1 from pg_catalog.pg_attribute
              where attrelid='public.offer'::regclass
                and attname='billing_period' and not attisdropped)
            and exists(select 1 from pg_catalog.pg_constraint
              where conrelid='public.offer'::regclass
                and conname='offer_status_check' and convalidated)
            and to_regprocedure(
              'app_private.transition_offer_status(uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid)'
            ) is not null
        $$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        """
        do $migration$
        begin
          if exists (select 1 from public.offer where status <> 'draft') then
            raise exception
              'cannot downgrade 0019_offer_status: active or archived offers exist; status data must be preserved'
              using errcode = '55000';
          end if;
          if exists (
            select 1 from app_private.catalog_mutation_request
            where operation = 'offer.status.update'
          ) then
            raise exception
              'cannot downgrade 0019_offer_status: offer status transition history exists; idempotency data must be preserved'
              using errcode = '55000';
          end if;
        end $migration$
        """,
        """
        create or replace function app_private.schema_ready() returns boolean
        language sql stable security definer set search_path=pg_catalog,app_private as $$
          select app_private.schema_ready_base_0017()
            and exists(select 1 from pg_catalog.pg_attribute
              where attrelid='public.offer'::regclass
                and attname='unit_amount_minor' and not attisdropped)
            and exists(select 1 from pg_catalog.pg_attribute
              where attrelid='public.offer'::regclass
                and attname='currency' and not attisdropped)
            and exists(select 1 from pg_catalog.pg_attribute
              where attrelid='public.offer'::regclass
                and attname='billing_period' and not attisdropped)
        $$
        """,
        r"""
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0016()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status')$needle$,
            $replacement$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing')$replacement$
          );
          if changed = definition then
            raise exception 'cannot restore 0016 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;

          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status')$needle$,
            $replacement$version_num in ('0017_pdf_preraster','0018_offer_pricing')$replacement$
          );
          if changed = definition then
            raise exception 'cannot restore 0017 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        """
        drop function app_private.transition_offer_status(
          uuid, uuid, uuid, uuid, text, text, bigint, uuid, text, uuid
        )
        """,
        """
        alter table app_private.catalog_mutation_request
          drop constraint catalog_mutation_request_operation_check,
          add constraint catalog_mutation_request_operation_check
            check (operation in ('draft.update', 'product.publish'))
        """,
        """
        alter table offer
          drop constraint offer_status_check,
          add constraint offer_status_check check (status = 'draft')
        """,
    ]
    for statement in statements:
        op.execute(statement)
