"""Create buyer entitlements and protected learning assets.

Revision ID: 0006_demo_learning
Revises: 0005_curriculum
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_demo_learning"
down_revision: str | None = "0005_curriculum"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = [
        """
        create table buyer_entitlement (
          seller_organization_id uuid not null,
          id uuid not null,
          buyer_identity_user_id uuid not null references identity_user(id),
          catalog_product_id uuid not null,
          reference_key text not null check (
            reference_key = btrim(reference_key)
            and octet_length(reference_key) between 3 and 160
            and reference_key ~ '^[a-z0-9][a-z0-9._:-]+$'
          ),
          source text not null check (source in ('demo_seed','manual','billing')),
          status text not null check (status in ('active','revoked','expired')),
          progress_percent smallint not null default 0
            check (progress_percent between 0 and 100),
          starts_at timestamptz not null default clock_timestamp(),
          expires_at timestamptz,
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id,id),
          constraint buyer_entitlement_product_fkey
            foreign key (seller_organization_id,catalog_product_id)
            references catalog_product(seller_organization_id,id),
          constraint buyer_entitlement_subject_product_key
            unique (seller_organization_id,buyer_identity_user_id,catalog_product_id),
          constraint buyer_entitlement_reference_key
            unique (seller_organization_id,reference_key),
          constraint buyer_entitlement_expiry_check
            check (expires_at is null or expires_at > starts_at)
        )
        """,
        """
        create index buyer_entitlement_buyer_status_idx
          on buyer_entitlement (
            buyer_identity_user_id,status,expires_at,seller_organization_id,catalog_product_id
          )
        """,
        """
        alter table lesson add constraint lesson_seller_course_module_id_key
          unique (seller_organization_id,course_id,module_id,id)
        """,
        """
        create table learning_asset (
          seller_organization_id uuid not null,
          id uuid not null,
          catalog_product_id uuid not null,
          course_id uuid not null,
          module_id uuid not null,
          lesson_id uuid not null,
          seed_key text not null check (
            seed_key = btrim(seed_key)
            and octet_length(seed_key) between 3 and 120
            and seed_key ~ '^[a-z0-9][a-z0-9-]+$'
          ),
          file_name text not null check (
            file_name ~ '^[a-z0-9][a-z0-9-]+[.]pdf$'
            and octet_length(file_name) <= 128
          ),
          title text not null check (
            title = btrim(title) and octet_length(title) between 2 and 160
            and title !~ '[[:cntrl:]]'
          ),
          media_type text not null check (media_type = 'application/pdf'),
          byte_size integer not null check (byte_size between 1 and 10485760),
          sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
          position integer not null check (position >= 0),
          status text not null default 'active' check (status in ('active','archived')),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id,id),
          constraint learning_asset_product_course_fkey
            foreign key (seller_organization_id,catalog_product_id,course_id)
            references product_course(seller_organization_id,catalog_product_id,course_id),
          constraint learning_asset_lesson_fkey
            foreign key (seller_organization_id,course_id,module_id,lesson_id)
            references lesson(seller_organization_id,course_id,module_id,id),
          constraint learning_asset_seed_key unique (seller_organization_id,seed_key),
          constraint learning_asset_lesson_position_key
            unique (seller_organization_id,lesson_id,position)
        )
        """,
        """
        create index learning_asset_product_status_idx
          on learning_asset (
            seller_organization_id,catalog_product_id,status,course_id,module_id,lesson_id,position
          )
        """,
        """
        create table app_private.demo_tenant_registry (
          seed_key text primary key check (seed_key = 'autonomia-demo-v1'),
          seed_version integer not null check (seed_version >= 1),
          manifest_sha256 text not null check (manifest_sha256 ~ '^[0-9a-f]{64}$'),
          identity_issuer text not null,
          admin_identity_user_id uuid not null references public.identity_user(id),
          buyer_identity_user_id uuid not null references public.identity_user(id),
          seller_organization_id uuid not null references public.seller_organization(id),
          catalog_product_id uuid not null,
          course_id uuid not null,
          offer_id uuid not null,
          baseline_publication_id uuid not null,
          seeded_at timestamptz not null default clock_timestamp(),
          reset_at timestamptz not null default clock_timestamp(),
          constraint demo_registry_product_fkey
            foreign key (seller_organization_id,catalog_product_id)
            references public.catalog_product(seller_organization_id,id),
          constraint demo_registry_course_fkey
            foreign key (seller_organization_id,course_id)
            references public.course(seller_organization_id,id),
          constraint demo_registry_offer_fkey
            foreign key (seller_organization_id,catalog_product_id,offer_id)
            references public.offer(seller_organization_id,catalog_product_id,id),
          constraint demo_registry_publication_fkey
            foreign key (seller_organization_id,catalog_product_id,baseline_publication_id)
            references public.publication(seller_organization_id,catalog_product_id,id),
          constraint demo_registry_distinct_personas
            check (admin_identity_user_id <> buyer_identity_user_id)
        )
        """,
        "revoke all on table app_private.demo_tenant_registry from public,appsell_runtime",
        "alter table buyer_entitlement enable row level security",
        "alter table buyer_entitlement force row level security",
        "alter table learning_asset enable row level security",
        "alter table learning_asset force row level security",
        "create policy buyer_entitlement_owner_all on buyer_entitlement to appsell_owner using (true) with check (true)",
        "create policy learning_asset_owner_all on learning_asset to appsell_owner using (true) with check (true)",
        """
        create policy buyer_entitlement_subject_read on buyer_entitlement
          for select to appsell_runtime using (
            buyer_identity_user_id=app_private.context_uuid('identity_user_id')
            and app_private.context_text('capability')='library.read'
            and status='active'
            and (expires_at is null or expires_at>clock_timestamp())
          )
        """,
        """
        create policy learning_asset_subject_read on learning_asset
          for select to appsell_runtime using (
            app_private.context_text('capability') in ('library.read','library.asset.read')
            and status='active'
            and exists (
              select 1 from public.buyer_entitlement entitlement
              where entitlement.seller_organization_id=learning_asset.seller_organization_id
                and entitlement.catalog_product_id=learning_asset.catalog_product_id
                and entitlement.buyer_identity_user_id=
                  app_private.context_uuid('identity_user_id')
                and entitlement.status='active'
                and (entitlement.expires_at is null
                  or entitlement.expires_at>clock_timestamp())
            )
          )
        """,
        """
        create function app_private.library_context_allowed(
          p_identity_user_id uuid,p_capability text
        ) returns boolean
        language sql stable security definer
        set search_path=pg_catalog,app_private,public as $$
          select p_identity_user_id is not null
            and p_capability in ('library.read','library.asset.read')
            and app_private.context_uuid('identity_user_id')=p_identity_user_id
            and app_private.context_text('capability')=p_capability
        $$
        """,
        "revoke all on function app_private.library_context_allowed(uuid,text) from public",
        """
        create function app_private.read_buyer_library(p_identity_user_id uuid)
        returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare result jsonb;
        begin
          if not app_private.library_context_allowed(p_identity_user_id,'library.read') then
            raise exception 'library context is not authorized' using errcode='28000';
          end if;
          select coalesce(jsonb_agg(jsonb_build_object(
            'seller',jsonb_build_object(
              'id',organization.id,'slug',organization.slug,'name',organization.display_name
            ),
            'product',jsonb_build_object(
              'id',product.id,'slug',product.slug,'title',product.title,
              'description',product.description,'publication_id',publication.id,
              'publication_version',publication.version
            ),
            'progress_percent',entitlement.progress_percent,
            'snapshot',publication.snapshot,
            'assets',coalesce((
              select jsonb_agg(jsonb_build_object(
                'id',asset.id,'lesson_id',asset.lesson_id,'seed_key',asset.seed_key,
                'title',asset.title,'media_type',asset.media_type,
                'byte_size',asset.byte_size,'sha256',asset.sha256,'position',asset.position
              ) order by asset.position,asset.id)
              from public.learning_asset asset
              where asset.seller_organization_id=entitlement.seller_organization_id
                and asset.catalog_product_id=entitlement.catalog_product_id
                and asset.status='active'
                and exists (
                  select 1 from jsonb_array_elements(publication.snapshot->'courses') course_item,
                    jsonb_array_elements(course_item->'modules') module_item,
                    jsonb_array_elements(module_item->'lessons') lesson_item
                  where lesson_item->>'id'=asset.lesson_id::text
                )
            ),'[]'::jsonb)
          ) order by entitlement.created_at,entitlement.id),'[]'::jsonb) into result
          from public.buyer_entitlement entitlement
          join public.seller_organization organization
            on organization.id=entitlement.seller_organization_id
          join public.catalog_product product
            on product.seller_organization_id=entitlement.seller_organization_id
           and product.id=entitlement.catalog_product_id
          join public.publication publication
            on publication.seller_organization_id=product.seller_organization_id
           and publication.catalog_product_id=product.id
           and publication.id=product.published_snapshot_id
          where entitlement.buyer_identity_user_id=p_identity_user_id
            and entitlement.status='active'
            and (entitlement.expires_at is null
              or entitlement.expires_at>clock_timestamp())
            and organization.status='active' and product.status='published';
          return result;
        end $$
        """,
        "revoke all on function app_private.read_buyer_library(uuid) from public",
        "grant execute on function app_private.read_buyer_library(uuid) to appsell_runtime",
        """
        create function app_private.authorize_learning_asset(
          p_identity_user_id uuid,p_seller_organization_id uuid,p_asset_id uuid
        ) returns table(
          asset_id uuid,seed_key text,file_name text,title text,media_type text,
          byte_size integer,sha256 text
        ) language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        begin
          if not app_private.library_context_allowed(
            p_identity_user_id,'library.asset.read'
          ) then
            raise exception 'asset context is not authorized' using errcode='28000';
          end if;
          return query select asset.id,asset.seed_key,asset.file_name,asset.title,
            asset.media_type,asset.byte_size,asset.sha256
          from public.learning_asset asset
          join public.seller_organization organization
            on organization.id=asset.seller_organization_id
          join public.buyer_entitlement entitlement
            on entitlement.seller_organization_id=asset.seller_organization_id
           and entitlement.catalog_product_id=asset.catalog_product_id
           and entitlement.buyer_identity_user_id=p_identity_user_id
          join public.catalog_product product
            on product.seller_organization_id=asset.seller_organization_id
           and product.id=asset.catalog_product_id
          join public.publication publication
            on publication.seller_organization_id=product.seller_organization_id
           and publication.catalog_product_id=product.id
           and publication.id=product.published_snapshot_id
          where asset.seller_organization_id=p_seller_organization_id
            and asset.id=p_asset_id and asset.status='active'
            and entitlement.status='active'
            and (entitlement.expires_at is null
              or entitlement.expires_at>clock_timestamp())
            and organization.status='active' and product.status='published'
            and exists (
              select 1 from jsonb_array_elements(publication.snapshot->'courses') course_item,
                jsonb_array_elements(course_item->'modules') module_item,
                jsonb_array_elements(module_item->'lessons') lesson_item
              where lesson_item->>'id'=asset.lesson_id::text
            );
          if not found then
            raise exception 'learning asset is unavailable' using errcode='28000';
          end if;
        end $$
        """,
        "revoke all on function app_private.authorize_learning_asset(uuid,uuid,uuid) from public",
        "grant execute on function app_private.authorize_learning_asset(uuid,uuid,uuid) to appsell_runtime",
        "grant select on buyer_entitlement,learning_asset to appsell_runtime",
        "revoke insert,update,delete on buyer_entitlement,learning_asset from appsell_runtime",
        """
        create or replace function app_private.reject_publication_mutation()
        returns trigger language plpgsql
        set search_path=pg_catalog as $$
        declare database_marker text;
        begin
          if tg_op='DELETE'
            and old.seller_organization_id='90718777-93de-5e7a-aaa4-45b25051582c'::uuid
            and current_setting('appsell.demo_reset_key',true)='autonomia-demo-v1'
          then
            select shobj_description(oid,'pg_database') into database_marker
            from pg_database where datname=current_database();
            if database_marker='appsell-demo-reset-target-v1' then
              return old;
            end if;
          end if;
          raise exception 'publications are append-only' using errcode='55000';
        end $$
        """,
        """
        create or replace function app_private.schema_ready() returns boolean
        language sql stable security definer set search_path=pg_catalog,app_private as $$
          select to_regclass('public.identity_user') is not null
            and to_regclass('public.catalog_product') is not null
            and to_regclass('public.course_module') is not null
            and to_regclass('public.lesson') is not null
            and to_regclass('public.buyer_entitlement') is not null
            and to_regclass('public.learning_asset') is not null
            and to_regclass('app_private.demo_tenant_registry') is not null
            and exists(select 1 from public.alembic_version
              where version_num='0006_demo_learning')
            and exists(select 1 from app_private.context_signing_key where singleton)
            and exists(select 1 from app_private.bootstrap_trust_policy where singleton)
        $$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("AppSell migrations are forward-only")
