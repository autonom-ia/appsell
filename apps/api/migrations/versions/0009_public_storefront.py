"""Create a seller-scoped public storefront projection.

Revision ID: 0009_public_storefront
Revises: 0008_private_asset_storage
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_public_storefront"
down_revision: str | None = "0008_private_asset_storage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = [
        """
        create index catalog_product_public_storefront_idx
          on catalog_product (seller_organization_id, updated_at desc, id desc)
          where status='published' and published_snapshot_id is not null
        """,
        """
        create table storefront_product_projection (
          seller_organization_id uuid not null,
          catalog_product_id uuid not null,
          publication_id uuid not null,
          product_slug text not null check (
            product_slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'
            and octet_length(product_slug) between 3 and 63
          ),
          title text not null check (
            title=btrim(title) and octet_length(title) between 2 and 160
            and title !~ '[[:cntrl:]]'
          ),
          description text not null check (
            octet_length(description)<=2000 and description !~ '[[:cntrl:]]'
          ),
          offer_title text not null check (
            offer_title=btrim(offer_title) and octet_length(offer_title) between 2 and 120
            and offer_title !~ '[[:cntrl:]]'
          ),
          offer_summary text not null check (
            octet_length(offer_summary)<=500 and offer_summary !~ '[[:cntrl:]]'
          ),
          courses jsonb not null check (
            jsonb_typeof(courses)='array' and octet_length(courses::text)<=524288
          ),
          course_count smallint not null check (
            course_count between 0 and 100 and course_count=jsonb_array_length(courses)
          ),
          projected_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id,catalog_product_id),
          constraint storefront_projection_product_fkey
            foreign key (seller_organization_id,catalog_product_id)
            references catalog_product(seller_organization_id,id),
          constraint storefront_projection_publication_fkey
            foreign key (seller_organization_id,catalog_product_id,publication_id)
            references publication(seller_organization_id,catalog_product_id,id),
          constraint storefront_projection_seller_slug_key
            unique (seller_organization_id,product_slug)
        )
        """,
        "alter table storefront_product_projection enable row level security",
        "alter table storefront_product_projection force row level security",
        """
        create policy storefront_projection_owner_all on storefront_product_projection
          to appsell_owner using (true) with check (true)
        """,
        "revoke all on table storefront_product_projection from public,appsell_runtime",
        """
        create function app_private.refresh_storefront_product_projection()
        returns trigger language plpgsql security definer
        set search_path=pg_catalog,app_private as $$
        declare published record; safe_courses jsonb:='[]'::jsonb; course_item jsonb;
          course_position numeric; product_slug text; product_title text;
          product_description text; offer_title text; offer_summary text;
        begin
          if new.status<>'published' or new.published_snapshot_id is null then
            delete from public.storefront_product_projection projection
            where projection.seller_organization_id=new.seller_organization_id
              and projection.catalog_product_id=new.id;
            return new;
          end if;
          select publication.id,publication.schema_version,publication.snapshot into published
          from public.publication publication
          where publication.seller_organization_id=new.seller_organization_id
            and publication.catalog_product_id=new.id
            and publication.id=new.published_snapshot_id;
          if not found then
            delete from public.storefront_product_projection projection
            where projection.seller_organization_id=new.seller_organization_id
              and projection.catalog_product_id=new.id;
            return new;
          end if;
          if published.schema_version not in (1,2,3)
             or jsonb_typeof(published.snapshot->'seller_organization_id')
                  is distinct from 'string'
             or published.snapshot#>>'{seller_organization_id}'
                  is distinct from new.seller_organization_id::text
             or jsonb_typeof(published.snapshot->'product') is distinct from 'object'
             or jsonb_typeof(published.snapshot->'offer') is distinct from 'object'
             or jsonb_typeof(published.snapshot->'courses') is distinct from 'array' then
            delete from public.storefront_product_projection projection
            where projection.seller_organization_id=new.seller_organization_id
              and projection.catalog_product_id=new.id;
            return new;
          end if;
          if jsonb_typeof(published.snapshot->'product'->'id') is distinct from 'string'
             or published.snapshot#>>'{product,id}' is distinct from new.id::text
             or jsonb_typeof(published.snapshot->'product'->'slug') is distinct from 'string'
             or jsonb_typeof(published.snapshot->'product'->'title') is distinct from 'string'
             or jsonb_typeof(published.snapshot->'product'->'description')
                  is distinct from 'string'
             or jsonb_typeof(published.snapshot->'offer'->'title') is distinct from 'string'
             or jsonb_typeof(published.snapshot->'offer'->'summary')
                  is distinct from 'string' then
            delete from public.storefront_product_projection projection
            where projection.seller_organization_id=new.seller_organization_id
              and projection.catalog_product_id=new.id;
            return new;
          end if;
          product_slug:=published.snapshot#>>'{product,slug}';
          product_title:=published.snapshot#>>'{product,title}';
          product_description:=published.snapshot#>>'{product,description}';
          offer_title:=published.snapshot#>>'{offer,title}';
          offer_summary:=published.snapshot#>>'{offer,summary}';
          if product_slug is null
             or product_slug !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'
             or octet_length(product_slug) not between 3 and 63
             or product_title is null or product_title<>btrim(product_title)
             or octet_length(product_title) not between 2 and 160
             or product_title ~ '[[:cntrl:]]'
             or product_description is null or octet_length(product_description)>2000
             or product_description ~ '[[:cntrl:]]'
             or offer_title is null or offer_title<>btrim(offer_title)
             or octet_length(offer_title) not between 2 and 120
             or offer_title ~ '[[:cntrl:]]'
             or offer_summary is null or octet_length(offer_summary)>500
             or offer_summary ~ '[[:cntrl:]]'
             or jsonb_array_length(published.snapshot->'courses')>100 then
            delete from public.storefront_product_projection projection
            where projection.seller_organization_id=new.seller_organization_id
              and projection.catalog_product_id=new.id;
            return new;
          end if;
          for course_item in
            select value from jsonb_array_elements(published.snapshot->'courses')
          loop
            if jsonb_typeof(course_item) is distinct from 'object'
               or jsonb_typeof(course_item->'title') is distinct from 'string'
               or jsonb_typeof(course_item->'description') is distinct from 'string'
               or course_item->>'title'<>btrim(course_item->>'title')
               or octet_length(course_item->>'title') not between 2 and 160
               or course_item->>'title' ~ '[[:cntrl:]]'
               or octet_length(course_item->>'description')>2000
               or course_item->>'description' ~ '[[:cntrl:]]'
               or course_item->>'position' is null
               or course_item->>'position' !~ '^(0|[1-9][0-9]{0,9})$' then
              delete from public.storefront_product_projection projection
              where projection.seller_organization_id=new.seller_organization_id
                and projection.catalog_product_id=new.id;
              return new;
            end if;
            course_position:=(course_item->>'position')::numeric;
            if course_position>2147483647 then
              delete from public.storefront_product_projection projection
              where projection.seller_organization_id=new.seller_organization_id
                and projection.catalog_product_id=new.id;
              return new;
            end if;
            safe_courses:=safe_courses||jsonb_build_array(jsonb_build_object(
              'title',course_item->>'title','description',course_item->>'description',
              'position',course_position::integer
            ));
          end loop;
          if octet_length(safe_courses::text)>524288 then
            delete from public.storefront_product_projection projection
            where projection.seller_organization_id=new.seller_organization_id
              and projection.catalog_product_id=new.id;
            return new;
          end if;
          insert into public.storefront_product_projection(
            seller_organization_id,catalog_product_id,publication_id,product_slug,
            title,description,offer_title,offer_summary,courses,course_count,projected_at
          ) values(
            new.seller_organization_id,new.id,published.id,product_slug,product_title,
            product_description,offer_title,offer_summary,safe_courses,
            jsonb_array_length(safe_courses),clock_timestamp()
          ) on conflict(seller_organization_id,catalog_product_id) do update set
            publication_id=excluded.publication_id,product_slug=excluded.product_slug,
            title=excluded.title,description=excluded.description,
            offer_title=excluded.offer_title,offer_summary=excluded.offer_summary,
            courses=excluded.courses,course_count=excluded.course_count,
            projected_at=excluded.projected_at;
          return new;
        end $$
        """,
        "revoke all on function app_private.refresh_storefront_product_projection() from public,appsell_runtime",
        """
        create trigger catalog_product_storefront_projection
          after update of status,published_snapshot_id on catalog_product
          for each row execute function app_private.refresh_storefront_product_projection()
        """,
        """
        update catalog_product set status=status
        where status='published' and published_snapshot_id is not null
        """,
        """
        create function app_private.public_storefront_context_allowed()
        returns boolean language sql stable security definer
        set search_path=pg_catalog,app_private as $$
          select app_private.context_text('capability')='storefront.public.read'
            and app_private.context_uuid('seller_organization_id') is null
        $$
        """,
        "revoke all on function app_private.public_storefront_context_allowed() from public,appsell_runtime",
        """
        create function app_private.read_public_storefront(
          p_seller_slug text,p_cursor text,p_limit integer
        ) returns jsonb language plpgsql stable security definer
        set search_path=pg_catalog,app_private as $$
        declare seller_id uuid; seller_name text; products jsonb; next_cursor text;
        begin
          if not app_private.public_storefront_context_allowed() then
            raise exception 'public storefront context is not authorized' using errcode='28000';
          end if;
          if p_seller_slug is null or p_seller_slug !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'
             or octet_length(p_seller_slug) not between 3 and 63
             or (p_cursor is not null and (
               p_cursor !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'
               or octet_length(p_cursor) not between 3 and 63
             )) or p_limit not between 1 and 24 then
            raise exception 'invalid public storefront input' using errcode='22023';
          end if;
          select organization.id,organization.display_name into seller_id,seller_name
          from public.seller_organization organization
          where organization.slug=p_seller_slug and organization.status='active';
          if not found then return null; end if;

          select coalesce(jsonb_agg(candidate.payload order by candidate.slug),'[]'::jsonb)
            into products
          from (
            select projection.product_slug slug,jsonb_build_object(
              'slug',projection.product_slug,
              'title',projection.title,
              'description',projection.description,
              'offer',jsonb_build_object(
                'title',projection.offer_title,
                'summary',projection.offer_summary
              ),
              'course_count',projection.course_count
            ) payload
            from public.storefront_product_projection projection
            join public.catalog_product product
              on product.seller_organization_id=projection.seller_organization_id
             and product.id=projection.catalog_product_id
             and product.published_snapshot_id=projection.publication_id
             and product.status='published'
            where projection.seller_organization_id=seller_id
              and (p_cursor is null or projection.product_slug>p_cursor)
            order by projection.product_slug
            limit p_limit
          ) candidate;
          if jsonb_array_length(products)=0 then return null; end if;

          select candidate.slug into next_cursor
          from (
            select projection.product_slug slug
            from public.storefront_product_projection projection
            join public.catalog_product product
              on product.seller_organization_id=projection.seller_organization_id
             and product.id=projection.catalog_product_id
             and product.published_snapshot_id=projection.publication_id
             and product.status='published'
            where projection.seller_organization_id=seller_id
              and (p_cursor is null or projection.product_slug>p_cursor)
            order by projection.product_slug
            offset p_limit limit 1
          ) candidate;
          if next_cursor is not null then
            next_cursor:=products->(jsonb_array_length(products)-1)->>'slug';
          end if;
          return jsonb_build_object(
            'seller',jsonb_build_object('slug',p_seller_slug,'name',seller_name),
            'products',products,
            'next_cursor',next_cursor
          );
        end $$
        """,
        "revoke all on function app_private.read_public_storefront(text,text,integer) from public,appsell_runtime",
        "grant execute on function app_private.read_public_storefront(text,text,integer) to appsell_runtime",
        """
        create function app_private.read_public_product(
          p_seller_slug text,p_product_slug text
        ) returns jsonb language plpgsql stable security definer
        set search_path=pg_catalog,app_private as $$
        declare seller_id uuid; seller_name text; product_payload jsonb; others jsonb;
        begin
          if not app_private.public_storefront_context_allowed() then
            raise exception 'public storefront context is not authorized' using errcode='28000';
          end if;
          if p_seller_slug is null or p_seller_slug !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'
             or octet_length(p_seller_slug) not between 3 and 63
             or p_product_slug is null
             or p_product_slug !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'
             or octet_length(p_product_slug) not between 3 and 63 then
            raise exception 'invalid public product input' using errcode='22023';
          end if;
          select organization.id,organization.display_name into seller_id,seller_name
          from public.seller_organization organization
          where organization.slug=p_seller_slug and organization.status='active';
          if not found then return null; end if;

          select jsonb_build_object(
            'slug',projection.product_slug,
            'title',projection.title,
            'description',projection.description,
            'offer',jsonb_build_object(
              'title',projection.offer_title,
              'summary',projection.offer_summary
            ),
            'courses',projection.courses
          ) into product_payload
          from public.storefront_product_projection projection
          join public.catalog_product product
            on product.seller_organization_id=projection.seller_organization_id
           and product.id=projection.catalog_product_id
           and product.published_snapshot_id=projection.publication_id
           and product.status='published'
          where projection.seller_organization_id=seller_id
            and projection.product_slug=p_product_slug;
          if product_payload is null then return null; end if;

          select coalesce(jsonb_agg(candidate.payload order by candidate.slug),'[]'::jsonb)
            into others
          from (
            select projection.product_slug slug,jsonb_build_object(
              'slug',projection.product_slug,
              'title',projection.title,
              'description',projection.description,
              'course_count',projection.course_count
            ) payload
            from public.storefront_product_projection projection
            join public.catalog_product product
              on product.seller_organization_id=projection.seller_organization_id
             and product.id=projection.catalog_product_id
             and product.published_snapshot_id=projection.publication_id
             and product.status='published'
            where projection.seller_organization_id=seller_id
              and projection.product_slug<>p_product_slug
            order by projection.product_slug limit 4
          ) candidate;
          return jsonb_build_object(
            'seller',jsonb_build_object('slug',p_seller_slug,'name',seller_name),
            'product',product_payload,
            'other_products',others
          );
        end $$
        """,
        "revoke all on function app_private.read_public_product(text,text) from public,appsell_runtime",
        "grant execute on function app_private.read_public_product(text,text) to appsell_runtime",
        """
        create or replace function app_private.schema_ready() returns boolean
        language sql stable security definer set search_path=pg_catalog,app_private as $$
          select to_regclass('public.identity_user') is not null
            and to_regclass('public.asset_record') is not null
            and to_regclass('public.asset_blob') is not null
            and to_regclass('public.asset_version') is not null
            and to_regclass('public.lesson_asset_draft') is not null
            and to_regclass('public.publication_asset') is not null
            and to_regclass('public.storefront_product_projection') is not null
            and to_regclass('app_private.asset_upload') is not null
            and to_regclass('app_private.asset_verification_job') is not null
            and to_regclass('app_private.asset_storage_cleanup_job') is not null
            and to_regclass('app_private.asset_mutation_request') is not null
            and to_regclass('app_private.asset_worker_identity') is not null
            and to_regprocedure('app_private.read_public_storefront(text,text,integer)') is not null
            and to_regprocedure('app_private.read_public_product(text,text)') is not null
            and exists(select 1 from public.alembic_version
              where version_num='0009_public_storefront')
            and exists(select 1 from app_private.context_signing_key where singleton)
            and exists(select 1 from app_private.bootstrap_trust_policy where singleton)
        $$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("AppSell migrations are forward-only")
