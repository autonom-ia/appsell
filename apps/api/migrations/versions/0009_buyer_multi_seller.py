"""Add canonical buyer-global product and seller-storefront projections.

Revision ID: 0009_buyer_multi_seller
Revises: 0008_private_asset_storage
"""

from alembic import op

revision: str = "0009_buyer_multi_seller"
down_revision: str | None = "0008_private_asset_storage"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    statements = [
        """
        create function app_private.read_buyer_products(p_actor uuid)
        returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        begin
          if not app_private.library_context_allowed(p_actor,'library.read') then
            raise exception 'buyer products context is not authorized' using errcode='28000';
          end if;
          return app_private.read_buyer_library(p_actor);
        end $$
        """,
        "revoke all on function app_private.read_buyer_products(uuid) from public",
        "grant execute on function app_private.read_buyer_products(uuid) to appsell_runtime",
        """
        create function app_private.read_buyer_seller_storefront(
          p_actor uuid,p_entitlement uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare target_seller uuid; purchased_product uuid; result jsonb;
        begin
          if not app_private.library_context_allowed(p_actor,'library.read')
             or p_entitlement is null then
            raise exception 'buyer storefront context is not authorized' using errcode='28000';
          end if;
          select entitlement.seller_organization_id,entitlement.catalog_product_id
          into target_seller,purchased_product
          from buyer_entitlement entitlement
          join seller_organization organization
            on organization.id=entitlement.seller_organization_id
          join catalog_product product
            on product.seller_organization_id=entitlement.seller_organization_id
           and product.id=entitlement.catalog_product_id
          where entitlement.id=p_entitlement
            and entitlement.buyer_identity_user_id=p_actor
            and entitlement.status='active' and entitlement.starts_at<=clock_timestamp()
            and (entitlement.expires_at is null or entitlement.expires_at>clock_timestamp())
            and organization.status='active' and product.status='published';
          if not found then
            raise exception 'buyer storefront is unavailable' using errcode='28000';
          end if;
          select coalesce(jsonb_agg(jsonb_build_object(
            'seller',jsonb_build_object(
              'id',organization.id,'slug',organization.slug,'name',organization.display_name
            ),
            'product',jsonb_build_object(
              'id',product.id,'slug',publication.snapshot->'product'->>'slug',
              'title',publication.snapshot->'product'->>'title',
              'description',coalesce(publication.snapshot->'product'->>'description',''),
              'publication_id',publication.id,'publication_version',publication.version
            )
          ) order by product.created_at,product.id),'[]'::jsonb) into result
          from catalog_product product
          join seller_organization organization on organization.id=product.seller_organization_id
          join publication
            on publication.seller_organization_id=product.seller_organization_id
           and publication.catalog_product_id=product.id
           and publication.id=product.published_snapshot_id
          where product.seller_organization_id=target_seller
            and product.id<>purchased_product and product.status='published'
            and organization.status='active';
          return result;
        end $$
        """,
        "revoke all on function app_private.read_buyer_seller_storefront(uuid,uuid) from public",
        "grant execute on function app_private.read_buyer_seller_storefront(uuid,uuid) to appsell_runtime",
        """
        create or replace function app_private.schema_ready() returns boolean
        language sql stable security definer set search_path=pg_catalog,app_private as $$
          select to_regclass('public.identity_user') is not null
            and to_regclass('public.asset_record') is not null
            and to_regclass('public.asset_blob') is not null
            and to_regclass('public.asset_version') is not null
            and to_regclass('public.lesson_asset_draft') is not null
            and to_regclass('public.publication_asset') is not null
            and to_regclass('app_private.asset_upload') is not null
            and to_regclass('app_private.asset_verification_job') is not null
            and to_regclass('app_private.asset_storage_cleanup_job') is not null
            and to_regclass('app_private.asset_mutation_request') is not null
            and to_regclass('app_private.asset_worker_identity') is not null
            and to_regprocedure('app_private.read_buyer_products(uuid)') is not null
            and to_regprocedure(
              'app_private.read_buyer_seller_storefront(uuid,uuid)'
            ) is not null
            and exists(select 1 from public.alembic_version
              where version_num='0009_buyer_multi_seller')
            and exists(select 1 from app_private.context_signing_key where singleton)
            and exists(select 1 from app_private.bootstrap_trust_policy where singleton)
        $$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("AppSell migrations are forward-only")
