"""Create seller-scoped product and course drafts.

Revision ID: 0003_product_course_drafts
Revises: 0002_bff_api_onboarding
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_product_course_drafts"
down_revision: str | None = "0002_bff_api_onboarding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = [
        """
        create table catalog_product (
          seller_organization_id uuid not null references seller_organization(id),
          id uuid not null default gen_random_uuid(),
          slug text not null check (
            slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'
            and octet_length(slug) between 3 and 63
          ),
          title text not null check (
            title = btrim(title)
            and octet_length(title) between 2 and 160
            and title !~ '[[:cntrl:]]'
          ),
          description text not null default '' check (
            octet_length(description) <= 2000
            and description !~ '[[:cntrl:]]'
          ),
          status text not null default 'draft'
            check (status in ('draft', 'published', 'unpublished', 'archived')),
          version bigint not null default 1 check (version > 0),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id, id),
          constraint catalog_product_seller_slug_key
            unique (seller_organization_id, slug)
        )
        """,
        """
        create index catalog_product_seller_created_idx
          on catalog_product (seller_organization_id, created_at desc, id desc)
        """,
        """
        create table course (
          seller_organization_id uuid not null references seller_organization(id),
          id uuid not null default gen_random_uuid(),
          title text not null check (
            title = btrim(title)
            and octet_length(title) between 2 and 160
            and title !~ '[[:cntrl:]]'
          ),
          description text not null default '' check (
            octet_length(description) <= 2000
            and description !~ '[[:cntrl:]]'
          ),
          status text not null default 'draft'
            check (status in ('draft', 'published', 'archived')),
          draft_version bigint not null default 1 check (draft_version > 0),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id, id)
        )
        """,
        """
        create index course_seller_created_idx
          on course (seller_organization_id, created_at desc, id desc)
        """,
        """
        create table product_course (
          seller_organization_id uuid not null,
          catalog_product_id uuid not null,
          course_id uuid not null,
          position integer not null default 0 check (position >= 0),
          created_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id, catalog_product_id, course_id),
          constraint product_course_product_fkey
            foreign key (seller_organization_id, catalog_product_id)
            references catalog_product (seller_organization_id, id),
          constraint product_course_course_fkey
            foreign key (seller_organization_id, course_id)
            references course (seller_organization_id, id),
          constraint product_course_product_position_key
            unique (seller_organization_id, catalog_product_id, position)
        )
        """,
        """
        create index product_course_course_idx
          on product_course (seller_organization_id, course_id)
        """,
        """
        create table app_private.product_draft_request (
          seller_organization_id uuid not null,
          idempotency_key uuid not null,
          identity_user_id uuid not null references public.identity_user(id),
          body_sha256 text not null check (body_sha256 ~ '^[0-9a-f]{64}$'),
          catalog_product_id uuid not null,
          course_id uuid not null,
          created_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id, idempotency_key),
          constraint product_draft_request_product_fkey
            foreign key (seller_organization_id, catalog_product_id)
            references public.catalog_product (seller_organization_id, id),
          constraint product_draft_request_course_fkey
            foreign key (seller_organization_id, course_id)
            references public.course (seller_organization_id, id)
        )
        """,
        """
        create index product_draft_request_actor_idx
          on app_private.product_draft_request (
            identity_user_id, seller_organization_id, created_at desc
          )
        """,
        """
        create index product_draft_request_product_idx
          on app_private.product_draft_request (
            seller_organization_id, catalog_product_id
          )
        """,
        "revoke all on table app_private.product_draft_request from public, appsell_runtime",
        "alter table catalog_product enable row level security",
        "alter table catalog_product force row level security",
        "alter table course enable row level security",
        "alter table course force row level security",
        "alter table product_course enable row level security",
        "alter table product_course force row level security",
        """
        create function app_private.catalog_draft_read_allowed(
          p_seller_organization_id uuid
        ) returns boolean
        language sql stable security definer
        set search_path = pg_catalog, app_private, public
        as $$
          select p_seller_organization_id is not null
            and app_private.context_text('capability') = 'catalog.draft.read'
            and app_private.context_uuid('seller_organization_id') =
                p_seller_organization_id
            and exists (
              select 1
              from public.seller_organization organization
              join public.seller_membership membership
                on membership.seller_organization_id = organization.id
              where organization.id = p_seller_organization_id
                and organization.status = 'active'
                and membership.identity_user_id =
                    app_private.context_uuid('identity_user_id')
                and membership.role = 'seller_owner'
                and membership.status = 'active'
            )
        $$
        """,
        "revoke all on function app_private.catalog_draft_read_allowed(uuid) from public",
        "grant execute on function app_private.catalog_draft_read_allowed(uuid) to appsell_runtime",
        """
        create policy catalog_product_owner_all on catalog_product to appsell_owner
          using (true) with check (true)
        """,
        """
        create policy course_owner_all on course to appsell_owner
          using (true) with check (true)
        """,
        """
        create policy product_course_owner_all on product_course to appsell_owner
          using (true) with check (true)
        """,
        """
        create policy catalog_product_owner_read on catalog_product
          for select to appsell_runtime
          using (
            seller_organization_id =
              app_private.context_uuid('seller_organization_id')
            and app_private.catalog_draft_read_allowed(seller_organization_id)
          )
        """,
        """
        create policy course_owner_read on course
          for select to appsell_runtime
          using (
            seller_organization_id =
              app_private.context_uuid('seller_organization_id')
            and app_private.catalog_draft_read_allowed(seller_organization_id)
          )
        """,
        """
        create policy product_course_owner_read on product_course
          for select to appsell_runtime
          using (
            seller_organization_id =
              app_private.context_uuid('seller_organization_id')
            and app_private.catalog_draft_read_allowed(seller_organization_id)
          )
        """,
        """
        create function app_private.create_product_course_draft(
          p_identity_user_id uuid,
          p_seller_organization_id uuid,
          p_product_slug text,
          p_product_title text,
          p_product_description text,
          p_course_title text,
          p_idempotency_key uuid,
          p_body_sha256 text,
          p_request_id uuid
        ) returns table (
          catalog_product_id uuid,
          course_id uuid,
          created boolean
        )
        language plpgsql security definer
        set search_path = pg_catalog, app_private, public
        as $$
        declare
          existing_request app_private.product_draft_request%rowtype;
          new_product_id uuid;
          new_course_id uuid;
        begin
          if app_private.context_text('capability') <> 'catalog.draft.create'
             or app_private.context_uuid('identity_user_id') is distinct from
                p_identity_user_id
             or app_private.context_uuid('seller_organization_id') is distinct from
                p_seller_organization_id then
            raise exception 'catalog draft context is not authorized'
              using errcode = '28000';
          end if;
          if p_identity_user_id is null or p_seller_organization_id is null
             or p_idempotency_key is null or p_request_id is null
             or p_product_slug is null
             or p_product_slug !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'
             or octet_length(p_product_slug) not between 3 and 63
             or p_product_title is null
             or p_product_title <> btrim(p_product_title)
             or octet_length(p_product_title) not between 2 and 160
             or p_product_title ~ '[[:cntrl:]]'
             or p_product_description is null
             or octet_length(p_product_description) > 2000
             or p_product_description ~ '[[:cntrl:]]'
             or p_course_title is null
             or p_course_title <> btrim(p_course_title)
             or octet_length(p_course_title) not between 2 and 160
             or p_course_title ~ '[[:cntrl:]]'
             or p_body_sha256 is null
             or p_body_sha256 !~ '^[0-9a-f]{64}$' then
            raise exception 'invalid product draft request' using errcode = '22023';
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
          for no key update of organization, membership;
          if not found then
            raise exception 'catalog draft owner is not authorized'
              using errcode = '28000';
          end if;

          perform pg_advisory_xact_lock(
            hashtextextended(
              'appsell.catalog.draft:' || p_seller_organization_id::text || ':' ||
              p_idempotency_key::text,
              0
            )
          );
          select * into existing_request
          from app_private.product_draft_request request
          where request.seller_organization_id = p_seller_organization_id
            and request.idempotency_key = p_idempotency_key;
          if found then
            if existing_request.identity_user_id = p_identity_user_id
               and existing_request.body_sha256 = p_body_sha256 then
              return query select existing_request.catalog_product_id,
                                  existing_request.course_id, false;
              return;
            end if;
            raise exception 'product draft idempotency conflict'
              using errcode = '23505';
          end if;

          insert into public.catalog_product (
            seller_organization_id, slug, title, description, status
          ) values (
            p_seller_organization_id, p_product_slug, p_product_title,
            p_product_description, 'draft'
          ) returning id into new_product_id;
          insert into public.course (
            seller_organization_id, title, description, status
          ) values (
            p_seller_organization_id, p_course_title, '', 'draft'
          ) returning id into new_course_id;
          insert into public.product_course (
            seller_organization_id, catalog_product_id, course_id, position
          ) values (
            p_seller_organization_id, new_product_id, new_course_id, 0
          );
          insert into app_private.product_draft_request (
            seller_organization_id, idempotency_key, identity_user_id,
            body_sha256, catalog_product_id, course_id
          ) values (
            p_seller_organization_id, p_idempotency_key, p_identity_user_id,
            p_body_sha256, new_product_id, new_course_id
          );
          insert into public.audit_event (
            seller_organization_id, actor_identity_user_id, action, target_type,
            target_id, result, request_id, metadata
          ) values (
            p_seller_organization_id, p_identity_user_id,
            'catalog.product_draft_created', 'catalog_product', new_product_id,
            'succeeded', p_request_id,
            jsonb_build_object('slug', p_product_slug, 'course_id', new_course_id)
          );
          return query select new_product_id, new_course_id, true;
        end
        $$
        """,
        """
        revoke all on function app_private.create_product_course_draft(
          uuid, uuid, text, text, text, text, uuid, text, uuid
        ) from public
        """,
        """
        grant execute on function app_private.create_product_course_draft(
          uuid, uuid, text, text, text, text, uuid, text, uuid
        ) to appsell_runtime
        """,
        "grant select on catalog_product, course, product_course to appsell_runtime",
        "revoke insert, update, delete on catalog_product, course, product_course from appsell_runtime",
        """
        create or replace function app_private.schema_ready()
        returns boolean language sql stable security definer
        set search_path = pg_catalog, app_private
        as $$
          select to_regclass('public.identity_user') is not null
            and to_regclass('public.platform_grant') is not null
            and to_regclass('public.superadmin_bootstrap') is not null
            and to_regclass('public.seller_organization') is not null
            and to_regclass('public.seller_membership') is not null
            and to_regclass('public.audit_event') is not null
            and to_regclass('public.catalog_product') is not null
            and to_regclass('public.course') is not null
            and to_regclass('public.product_course') is not null
            and to_regclass('app_private.bff_request_replay') is not null
            and to_regclass('app_private.seller_onboarding_request') is not null
            and to_regclass('app_private.product_draft_request') is not null
            and exists (
              select 1 from public.alembic_version
              where version_num = '0003_product_course_drafts'
            )
            and exists (select 1 from app_private.context_signing_key where singleton)
            and exists (select 1 from app_private.bootstrap_trust_policy where singleton)
        $$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("AppSell migrations are forward-only")
