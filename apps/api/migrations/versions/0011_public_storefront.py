"""Expose a read-only public storefront projection behind the private API.

Revision ID: 0011_public_storefront
Revises: 0010_release_candidate
"""

from alembic import op

revision: str = "0011_public_storefront"
down_revision: str | None = "0010_release_candidate"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    statements = [
        """
        create function app_private.read_public_storefront(p_seller_slug text)
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
            'publication_version',publication.version
          ) order by product.created_at,product.id),'[]'::jsonb)
          into products
          from catalog_product product
          join publication
            on publication.seller_organization_id=product.seller_organization_id
           and publication.catalog_product_id=product.id
           and publication.id=product.published_snapshot_id
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
        "revoke all on function app_private.read_public_storefront(text) from public",
        "grant execute on function app_private.read_public_storefront(text) to appsell_runtime",
        """
        create or replace function app_private.schema_ready() returns boolean
        language sql stable security definer set search_path=pg_catalog,app_private as $$
          select to_regclass('public.identity_user') is not null
            and to_regclass('public.catalog_product') is not null
            and to_regclass('public.publication') is not null
            and to_regclass('app_private.demo_tenant_registry') is not null
            and to_regclass('app_private.demo_inspection_ticket') is not null
            and to_regprocedure('app_private.read_public_storefront(text)') is not null
            and exists(select 1 from public.alembic_version
              where version_num='0011_public_storefront')
            and exists(select 1 from app_private.context_signing_key where singleton)
            and exists(select 1 from app_private.bootstrap_trust_policy where singleton)
        $$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("AppSell migrations are forward-only")
