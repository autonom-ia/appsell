"""Add real price, currency, and billing period to seller offers.

Revision ID: 0018_offer_pricing
Revises: 0017_pdf_preraster

Forward-only, additive change. The offer stays locked to ``status = 'draft'``;
unlocking a ``draft -> active`` transition is deferred because ``publish_product``
still requires the offer to be ``draft`` to be publishable and freezes the offer
into the immutable publication snapshot. Freezing price into that snapshot is a
follow-up: this migration keeps the snapshot bytes untouched so the migration
immutability guarantee (0004 -> head) holds, and exposes the live offer price on
the public storefront only for published products.

Price is optional when saving a draft: the atomic ``update_product_course_offer_draft``
function DEFAULTS an omitted price on create (0 / BRL / one_time) and PRESERVES the
stored value on update, so an editorial PATCH that carries no price can never wipe an
already-priced offer.

Because billing is not connected yet, the seller editor does not collect a price, so
newly-saved and pre-existing (legacy) offers carry ``unit_amount_minor = 0``. That
zero means "price pending", not "free": ``read_public_storefront`` emits ``price:
null`` for unpriced offers (only ``unit_amount_minor > 0`` produces a price object),
so no published product ever advertises R$ 0,00. Rendering a real price is a follow-up
gated on connecting billing and the ``draft -> active`` transition.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018_offer_pricing"
down_revision: str | None = "0017_pdf_preraster"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# R$ 999,999,999.99 expressed in minor units (centavos). Guards against overflow
# and absurd values while leaving ample headroom for any digital product. The
# same ceiling is enforced in the API layer (catalog_routes._MAX_UNIT_AMOUNT_MINOR).


def upgrade() -> None:
    statements = [
        """
        alter table offer
          add column unit_amount_minor bigint not null default 0
            check (unit_amount_minor >= 0
                   and unit_amount_minor <= 99999999999),
          add column currency char(3) not null default 'BRL'
            check (currency ~ '^[A-Z]{3}$'),
          add column billing_period text not null default 'one_time'
            check (billing_period in ('one_time', 'monthly', 'yearly'))
        """,
        """
        drop function app_private.update_product_course_offer_draft(
          uuid, uuid, uuid, text, uuid, uuid, text, text, text, text, text,
          bigint, bigint, bigint, uuid, text, uuid
        )
        """,
        """
        create function app_private.update_product_course_offer_draft(
          p_identity_user_id uuid,
          p_seller_organization_id uuid,
          p_catalog_product_id uuid,
          p_product_slug text,
          p_course_id uuid,
          p_offer_id uuid,
          p_product_title text,
          p_product_description text,
          p_course_title text,
          p_offer_title text,
          p_offer_summary text,
          p_offer_unit_amount_minor bigint,
          p_offer_currency text,
          p_offer_billing_period text,
          p_expected_product_version bigint,
          p_expected_course_version bigint,
          p_expected_offer_version bigint,
          p_idempotency_key uuid,
          p_body_sha256 text,
          p_request_id uuid
        ) returns table (
          offer_id uuid,
          product_version bigint,
          course_version bigint,
          offer_version bigint,
          created boolean
        )
        language plpgsql security definer
        set search_path = pg_catalog, app_private, public
        as $$
        declare
          existing_request app_private.catalog_mutation_request%rowtype;
          locked_product public.catalog_product%rowtype;
          locked_course public.course%rowtype;
          locked_offer public.offer%rowtype;
          next_product_version bigint;
          next_course_version bigint;
          next_offer_version bigint;
          eff_unit_amount_minor bigint;
          eff_currency text;
          eff_billing_period text;
          response jsonb;
        begin
          if app_private.context_text('capability') <> 'catalog.draft.update'
             or app_private.context_uuid('identity_user_id') is distinct from
                p_identity_user_id
             or app_private.context_uuid('seller_organization_id') is distinct from
                p_seller_organization_id then
            raise exception 'catalog draft update context is not authorized'
              using errcode = '28000';
          end if;
          if p_identity_user_id is null or p_seller_organization_id is null
             or p_catalog_product_id is null or p_course_id is null
             or p_offer_id is null or p_idempotency_key is null
             or p_request_id is null
             or p_product_slug is null
             or p_product_slug !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'
             or octet_length(p_product_slug) not between 3 and 63
             or p_product_title is null or p_product_title <> btrim(p_product_title)
             or octet_length(p_product_title) not between 2 and 160
             or p_product_title ~ '[[:cntrl:]]'
             or p_product_description is null
             or octet_length(p_product_description) > 2000
             or p_product_description ~ '[[:cntrl:]]'
             or p_course_title is null or p_course_title <> btrim(p_course_title)
             or octet_length(p_course_title) not between 2 and 160
             or p_course_title ~ '[[:cntrl:]]'
             or p_offer_title is null or p_offer_title <> btrim(p_offer_title)
             or octet_length(p_offer_title) not between 2 and 120
             or p_offer_title ~ '[[:cntrl:]]'
             or p_offer_summary is null or octet_length(p_offer_summary) > 500
             or p_offer_summary ~ '[[:cntrl:]]'
             or (p_offer_unit_amount_minor is not null
                 and (p_offer_unit_amount_minor < 0
                      or p_offer_unit_amount_minor > 99999999999))
             or (p_offer_currency is not null and p_offer_currency !~ '^[A-Z]{3}$')
             or (p_offer_billing_period is not null
                 and p_offer_billing_period not in ('one_time', 'monthly', 'yearly'))
             or p_expected_product_version is null or p_expected_product_version < 1
             or p_expected_course_version is null or p_expected_course_version < 1
             or p_expected_offer_version is null or p_expected_offer_version < 0
             or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$' then
            raise exception 'invalid catalog draft update request' using errcode = '22023';
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
            raise exception 'catalog draft owner is not authorized'
              using errcode = '28000';
          end if;

          perform pg_advisory_xact_lock(
            hashtextextended(
              'appsell.catalog.mutation:' || p_seller_organization_id::text ||
              '/draft.update/' || p_idempotency_key::text,
              0
            )
          );
          select * into existing_request
          from app_private.catalog_mutation_request request
          where request.seller_organization_id = p_seller_organization_id
            and request.operation = 'draft.update'
            and request.idempotency_key = p_idempotency_key;
          if found then
            if existing_request.identity_user_id = p_identity_user_id
               and existing_request.body_sha256 = p_body_sha256
               and existing_request.catalog_product_id = p_catalog_product_id
               and existing_request.offer_id = p_offer_id then
              return query select existing_request.offer_id,
                (existing_request.result ->> 'product_version')::bigint,
                (existing_request.result ->> 'course_version')::bigint,
                (existing_request.result ->> 'offer_version')::bigint,
                false;
              return;
            end if;
            raise exception 'catalog draft update idempotency conflict'
              using errcode = '23505';
          end if;

          select * into locked_product
          from public.catalog_product product
          where product.seller_organization_id = p_seller_organization_id
            and product.id = p_catalog_product_id
          for update;
          if not found then
            raise exception 'catalog product is unavailable' using errcode = '28000';
          end if;
          if locked_product.status = 'archived' then
            raise exception 'archived catalog product is immutable' using errcode = '55000';
          end if;
          if locked_product.version <> p_expected_product_version then
            raise exception 'catalog product version conflict' using errcode = '40001';
          end if;

          select course.* into locked_course
          from public.product_course linked
          join public.course course
            on course.seller_organization_id = linked.seller_organization_id
           and course.id = linked.course_id
          where linked.seller_organization_id = p_seller_organization_id
            and linked.catalog_product_id = p_catalog_product_id
            and linked.course_id = p_course_id
          for no key update of linked, course;
          if not found then
            raise exception 'catalog course is unavailable' using errcode = '28000';
          end if;
          if locked_course.status = 'archived' then
            raise exception 'archived course is immutable' using errcode = '55000';
          end if;
          if locked_course.draft_version <> p_expected_course_version then
            raise exception 'catalog course version conflict' using errcode = '40001';
          end if;

          select * into locked_offer
          from public.offer candidate
          where candidate.seller_organization_id = p_seller_organization_id
            and candidate.id = p_offer_id
          for update;
          if p_expected_offer_version = 0 then
            if found then
              raise exception 'catalog offer already exists' using errcode = '23505';
            end if;
            -- Create: an omitted price falls back to the column defaults.
            eff_unit_amount_minor := coalesce(p_offer_unit_amount_minor, 0);
            eff_currency := coalesce(p_offer_currency, 'BRL');
            eff_billing_period := coalesce(p_offer_billing_period, 'one_time');
            insert into public.offer (
              seller_organization_id, id, catalog_product_id, title, summary, status,
              unit_amount_minor, currency, billing_period
            ) values (
              p_seller_organization_id, p_offer_id, p_catalog_product_id,
              p_offer_title, p_offer_summary, 'draft',
              eff_unit_amount_minor, eff_currency, eff_billing_period
            );
            next_offer_version := 1;
          else
            if not found or locked_offer.catalog_product_id <> p_catalog_product_id then
              raise exception 'catalog offer is unavailable' using errcode = '28000';
            end if;
            if locked_offer.status <> 'draft' then
              raise exception 'catalog offer is not editable' using errcode = '55000';
            end if;
            if locked_offer.version <> p_expected_offer_version then
              raise exception 'catalog offer version conflict' using errcode = '40001';
            end if;
            -- Update: an omitted price PRESERVES the stored value, so an editorial
            -- PATCH that carries no price can never wipe an already-priced offer.
            eff_unit_amount_minor := coalesce(p_offer_unit_amount_minor,
                                              locked_offer.unit_amount_minor);
            eff_currency := coalesce(p_offer_currency, locked_offer.currency);
            eff_billing_period := coalesce(p_offer_billing_period,
                                           locked_offer.billing_period);
            update public.offer
            set title = p_offer_title,
                summary = p_offer_summary,
                unit_amount_minor = eff_unit_amount_minor,
                currency = eff_currency,
                billing_period = eff_billing_period,
                version = version + 1,
                updated_at = clock_timestamp()
            where seller_organization_id = p_seller_organization_id
              and id = p_offer_id
            returning version into next_offer_version;
          end if;

          update public.catalog_product
          set slug = p_product_slug,
              title = p_product_title,
              description = p_product_description,
              version = version + 1,
              updated_at = clock_timestamp()
          where seller_organization_id = p_seller_organization_id
            and id = p_catalog_product_id
          returning version into next_product_version;
          update public.course
          set title = p_course_title,
              draft_version = draft_version + 1,
              updated_at = clock_timestamp()
          where seller_organization_id = p_seller_organization_id
            and id = p_course_id
          returning draft_version into next_course_version;

          response := jsonb_build_object(
            'product_version', next_product_version,
            'course_version', next_course_version,
            'offer_version', next_offer_version
          );
          insert into app_private.catalog_mutation_request (
            seller_organization_id, operation, idempotency_key,
            identity_user_id, body_sha256, catalog_product_id, offer_id, result
          ) values (
            p_seller_organization_id, 'draft.update', p_idempotency_key,
            p_identity_user_id, p_body_sha256, p_catalog_product_id, p_offer_id,
            response
          );
          insert into public.audit_event (
            seller_organization_id, actor_identity_user_id, action, target_type,
            target_id, result, request_id, metadata
          ) values (
            p_seller_organization_id, p_identity_user_id,
            'catalog.product_draft_updated', 'catalog_product', p_catalog_product_id,
            'succeeded', p_request_id,
            jsonb_build_object(
              'course_id', p_course_id,
              'offer_id', p_offer_id,
              'product_version', next_product_version,
              'course_version', next_course_version,
              'offer_version', next_offer_version,
              'offer_unit_amount_minor', eff_unit_amount_minor,
              'offer_currency', eff_currency,
              'offer_billing_period', eff_billing_period
            )
          );
          return query select p_offer_id, next_product_version,
                              next_course_version, next_offer_version, true;
        end
        $$
        """,
        """
        revoke all on function app_private.update_product_course_offer_draft(
          uuid, uuid, uuid, text, uuid, uuid, text, text, text, text, text,
          bigint, text, text, bigint, bigint, bigint, uuid, text, uuid
        ) from public
        """,
        """
        grant execute on function app_private.update_product_course_offer_draft(
          uuid, uuid, uuid, text, uuid, uuid, text, text, text, text, text,
          bigint, text, text, bigint, bigint, bigint, uuid, text, uuid
        ) to appsell_runtime
        """,
        """
        create or replace function app_private.read_public_storefront(p_seller_slug text)
        returns jsonb language plpgsql stable security definer
        set search_path=pg_catalog,app_private,public as $$
        declare seller record; products jsonb;
        begin
          if p_seller_slug is null or p_seller_slug !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'
             or octet_length(p_seller_slug) not between 3 and 63 then
            return null;
          end if;
          select organization.id,organization.slug,organization.display_name
          into seller
          from seller_organization organization
          where organization.slug=p_seller_slug and organization.status='active';
          if not found then return null; end if;

          select coalesce(jsonb_agg(jsonb_build_object(
            'id',product.id,
            'slug',publication.snapshot->'product'->>'slug',
            'title',publication.snapshot->'product'->>'title',
            'description',coalesce(publication.snapshot->'product'->>'description',''),
            'publication_id',publication.id,
            'publication_version',publication.version,
            'price',case when offer.unit_amount_minor > 0
              then jsonb_build_object(
                'unit_amount_minor',offer.unit_amount_minor,
                'currency',rtrim(offer.currency),
                'billing_period',offer.billing_period
              )
              else null end
          ) order by product.created_at,product.id),'[]'::jsonb)
          into products
          from catalog_product product
          join publication
            on publication.seller_organization_id=product.seller_organization_id
           and publication.catalog_product_id=product.id
           and publication.id=product.published_snapshot_id
          join offer
            on offer.seller_organization_id=product.seller_organization_id
           and offer.catalog_product_id=product.id
          where product.seller_organization_id=seller.id
            and product.status='published';

          return jsonb_build_object(
            'schema_version',1,
            'seller',jsonb_build_object(
              'id',seller.id,'slug',seller.slug,'name',seller.display_name
            ),
            'products',products
          );
        end $$
        """,
        r"""
        do $migration$
        declare definition text; changed text;
        begin
          -- widen the transitively-called 0016 base so it accepts revision 0018
          definition:=pg_get_functiondef(
            'app_private.schema_ready_base_0016()'::regprocedure
          );
          changed:=replace(
            definition,
            $needle$version_num in ('0016_pdf_access_policy','0017_pdf_preraster')$needle$,
            $replacement$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing')$replacement$
          );
          if changed=definition then
            raise exception 'cannot extend 0016 schema readiness revision'
              using errcode='55000';
          end if;
          execute changed;

          -- wrap the current (0017) readiness and widen its own head assertion
          alter function app_private.schema_ready() rename to schema_ready_base_0017;
          definition:=pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed:=replace(
            definition,
            $needle$version_num='0017_pdf_preraster'$needle$,
            $replacement$version_num in ('0017_pdf_preraster','0018_offer_pricing')$replacement$
          );
          if changed=definition then
            raise exception 'cannot extend 0017 schema readiness revision'
              using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """,
        "revoke all on function app_private.schema_ready_base_0017() from public,appsell_runtime",
        """
        create function app_private.schema_ready() returns boolean
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
        "revoke all on function app_private.schema_ready() from public",
        "grant execute on function app_private.schema_ready() to appsell_runtime",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("AppSell migrations are forward-only")
