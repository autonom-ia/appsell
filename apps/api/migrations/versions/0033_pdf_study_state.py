"""Add private, version-pinned PDF annotations and bookmarks."""

from __future__ import annotations

from alembic import op

revision = "0033_pdf_study_state"
down_revision = "0032_platform_superadmin_catalog"
branch_labels = None
depends_on = None

_READY_TAIL = (
    "'0025_seller_membership_role','0026_asset_worker_claim_filename',"
    "'0027_product_course_draft_update','0028_offer_activation_guard',"
    "'0029_product_draft_asset_access','0030_product_pending_assets',"
    "'0031_public_free_access','0032_platform_superadmin_catalog')"
)
_READY_TAIL_WITH_0033 = _READY_TAIL[:-1] + ",'0033_pdf_study_state')"
_READY_ANCHOR = "and to_regprocedure('app_private.list_accessible_sellers(uuid)') is not null"
_READY_ADDITION = f"""{_READY_ANCHOR}
            and to_regclass('app_private.learning_pdf_study_state') is not null
            and to_regprocedure('app_private.read_pdf_study_state(uuid,uuid,uuid)') is not null
            and to_regprocedure('app_private.mutate_pdf_study_state(uuid,uuid,uuid,bigint,uuid,text,uuid,jsonb)') is not null"""

_LIBRARY_CONTEXT = """
create or replace function app_private.library_context_allowed(
  p_identity_user_id uuid,p_capability text
) returns boolean
language sql stable security definer
set search_path=pg_catalog,app_private,public as $$
  select coalesce(p_identity_user_id is not null
    and p_capability in (
      'library.read','library.asset.read','library.asset.download',
      'learning.pdf-study.read','learning.pdf-study.write'
    )
    and app_private.context_uuid('identity_user_id')=p_identity_user_id
    and app_private.context_uuid('seller_organization_id') is null
    and app_private.context_text('capability')=p_capability,false)
$$
"""

_LEGACY_LIBRARY_CONTEXT = """
create or replace function app_private.library_context_allowed(
  p_identity_user_id uuid,p_capability text
) returns boolean
language sql stable security definer
set search_path=pg_catalog,app_private,public as $$
  select coalesce(p_identity_user_id is not null
    and p_capability in ('library.read','library.asset.read','library.asset.download')
    and app_private.context_uuid('identity_user_id')=p_identity_user_id
    and app_private.context_uuid('seller_organization_id') is null
    and app_private.context_text('capability')=p_capability,false)
$$
"""


def upgrade() -> None:
    statements = [
        _LIBRARY_CONTEXT,
        """
        create table app_private.learning_pdf_study_state (
          seller_organization_id uuid not null,
          buyer_identity_user_id uuid not null references public.identity_user(id),
          entitlement_id uuid not null,
          asset_id uuid not null,
          asset_version integer not null check(asset_version>0),
          revision bigint not null default 0 check(revision>=0),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key(
            seller_organization_id,buyer_identity_user_id,entitlement_id,
            asset_id,asset_version
          ),
          foreign key(seller_organization_id,entitlement_id)
            references public.buyer_entitlement(seller_organization_id,id),
          foreign key(seller_organization_id,asset_id,asset_version)
            references public.asset_version(seller_organization_id,asset_id,version)
        )
        """,
        """
        create table app_private.learning_pdf_annotation (
          seller_organization_id uuid not null,
          buyer_identity_user_id uuid not null,
          entitlement_id uuid not null,
          asset_id uuid not null,
          asset_version integer not null,
          id uuid not null,
          page_number integer not null check(page_number between 1 and 100),
          tool text not null check(tool in ('pen','highlight')),
          color text not null check(color~'^#[0-9A-Fa-f]{6}$'),
          width double precision not null check(width between 0.0005 and 0.05),
          points jsonb not null check(jsonb_typeof(points)='array'),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key(
            seller_organization_id,buyer_identity_user_id,entitlement_id,
            asset_id,asset_version,id
          ),
          foreign key(
            seller_organization_id,buyer_identity_user_id,entitlement_id,
            asset_id,asset_version
          ) references app_private.learning_pdf_study_state(
            seller_organization_id,buyer_identity_user_id,entitlement_id,
            asset_id,asset_version
          ) on delete cascade
        )
        """,
        """
        create index learning_pdf_annotation_page_idx
          on app_private.learning_pdf_annotation(
            seller_organization_id,buyer_identity_user_id,entitlement_id,
            asset_id,asset_version,page_number,updated_at,id
          )
        """,
        """
        create table app_private.learning_pdf_bookmark (
          seller_organization_id uuid not null,
          buyer_identity_user_id uuid not null,
          entitlement_id uuid not null,
          asset_id uuid not null,
          asset_version integer not null,
          page_number integer not null check(page_number between 1 and 100),
          created_at timestamptz not null default clock_timestamp(),
          primary key(
            seller_organization_id,buyer_identity_user_id,entitlement_id,
            asset_id,asset_version,page_number
          ),
          foreign key(
            seller_organization_id,buyer_identity_user_id,entitlement_id,
            asset_id,asset_version
          ) references app_private.learning_pdf_study_state(
            seller_organization_id,buyer_identity_user_id,entitlement_id,
            asset_id,asset_version
          ) on delete cascade
        )
        """,
        """
        create table app_private.learning_pdf_study_request (
          actor_identity_user_id uuid not null,
          idempotency_key uuid not null,
          entitlement_id uuid not null,
          asset_id uuid not null,
          asset_version integer not null,
          body_sha256 text not null check(body_sha256~'^[0-9a-f]{64}$'),
          request_id uuid not null,
          result jsonb not null,
          created_at timestamptz not null default clock_timestamp(),
          primary key(actor_identity_user_id,idempotency_key),
          unique(request_id)
        )
        """,
        """
        create function app_private.authorize_pdf_study_asset(
          p_actor uuid,p_entitlement uuid,p_asset uuid,p_capability text
        ) returns table(
          seller_organization_id uuid,buyer_identity_user_id uuid,
          entitlement_id uuid,asset_id uuid,asset_version integer,page_count integer
        ) language plpgsql stable security definer
        set search_path=pg_catalog,app_private,public as $$
        begin
          if p_capability not in ('learning.pdf-study.read','learning.pdf-study.write')
             or not app_private.library_context_allowed(p_actor,p_capability) then
            raise exception 'PDF study context is not authorized' using errcode='28000';
          end if;
          return query
            select published.seller_organization_id,entitlement.buyer_identity_user_id,
              entitlement.id,published.asset_id,published.asset_version,raster.page_count
            from public.publication_asset published
            join public.buyer_entitlement entitlement
              on entitlement.seller_organization_id=published.seller_organization_id
             and entitlement.catalog_product_id=published.catalog_product_id
             and entitlement.publication_id=published.publication_id
             and entitlement.buyer_identity_user_id=p_actor
            join public.seller_organization organization
              on organization.id=published.seller_organization_id
            join public.catalog_product product
              on product.seller_organization_id=published.seller_organization_id
             and product.id=published.catalog_product_id
            join public.asset_pdf_raster raster
              on raster.seller_organization_id=published.seller_organization_id
             and raster.asset_id=published.asset_id
             and raster.asset_version=published.asset_version
             and raster.blob_id=published.blob_id
            where entitlement.id=p_entitlement and published.asset_id=p_asset
              and published.media_type='application/pdf'
              and entitlement.status='active' and entitlement.starts_at<=clock_timestamp()
              and (entitlement.expires_at is null or entitlement.expires_at>clock_timestamp())
              and organization.status='active' and product.status='published'
              and (select count(*)=raster.page_count
                  and min(page.page_number)=1
                  and max(page.page_number)=raster.page_count
                  and sum(page.page_number)=raster.page_count*(raster.page_count+1)/2
                  and sum(page.byte_size)=raster.total_byte_size
                from public.asset_pdf_page page
                where page.seller_organization_id=raster.seller_organization_id
                  and page.asset_id=raster.asset_id
                  and page.asset_version=raster.asset_version);
          if not found then
            raise exception 'PDF study asset is unavailable' using errcode='28000';
          end if;
        end $$
        """,
        """
        create function app_private.project_pdf_study_state(
          p_seller uuid,p_buyer uuid,p_entitlement uuid,p_asset uuid,p_version integer,
          p_page_count integer
        ) returns jsonb language sql stable security definer
        set search_path=pg_catalog,app_private,public as $$
          select jsonb_build_object(
            'asset_id',p_asset,'asset_version',p_version,'page_count',p_page_count,
            'revision',coalesce(state.revision,0),
            'annotations',coalesce((select jsonb_agg(jsonb_build_object(
              'id',annotation.id,'page',annotation.page_number,'tool',annotation.tool,
              'color',annotation.color,'width',annotation.width,'points',annotation.points
            ) order by annotation.page_number,annotation.created_at,annotation.id)
              from app_private.learning_pdf_annotation annotation
              where annotation.seller_organization_id=p_seller
                and annotation.buyer_identity_user_id=p_buyer
                and annotation.entitlement_id=p_entitlement
                and annotation.asset_id=p_asset and annotation.asset_version=p_version
            ),'[]'::jsonb),
            'bookmarks',coalesce((select jsonb_agg(bookmark.page_number order by bookmark.page_number)
              from app_private.learning_pdf_bookmark bookmark
              where bookmark.seller_organization_id=p_seller
                and bookmark.buyer_identity_user_id=p_buyer
                and bookmark.entitlement_id=p_entitlement
                and bookmark.asset_id=p_asset and bookmark.asset_version=p_version
            ),'[]'::jsonb),
            'updated_at',state.updated_at
          )
          from (select 1) seed
          left join app_private.learning_pdf_study_state state
            on state.seller_organization_id=p_seller
           and state.buyer_identity_user_id=p_buyer
           and state.entitlement_id=p_entitlement
           and state.asset_id=p_asset and state.asset_version=p_version
        $$
        """,
        """
        create function app_private.read_pdf_study_state(
          p_actor uuid,p_entitlement uuid,p_asset uuid
        ) returns jsonb language plpgsql stable security definer
        set search_path=pg_catalog,app_private,public as $$
        declare authorized record;
        begin
          select * into strict authorized from app_private.authorize_pdf_study_asset(
            p_actor,p_entitlement,p_asset,'learning.pdf-study.read'
          );
          return app_private.project_pdf_study_state(
            authorized.seller_organization_id,authorized.buyer_identity_user_id,
            authorized.entitlement_id,authorized.asset_id,authorized.asset_version,
            authorized.page_count
          );
        end $$
        """,
        """
        create function app_private.mutate_pdf_study_state(
          p_actor uuid,p_entitlement uuid,p_asset uuid,p_expected_revision bigint,
          p_idempotency uuid,p_body_sha256 text,p_request_id uuid,p_mutation jsonb
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare authorized record; current_revision bigint; replay record;
          action text; annotation jsonb; annotation_id uuid; page integer;
          result jsonb; invalid_points boolean;
        begin
          if p_expected_revision<0 or p_idempotency is null or p_request_id is null
             or p_body_sha256!~'^[0-9a-f]{64}$' or jsonb_typeof(p_mutation)<>'object' then
            raise exception 'invalid PDF study request' using errcode='22023';
          end if;
          select * into strict authorized from app_private.authorize_pdf_study_asset(
            p_actor,p_entitlement,p_asset,'learning.pdf-study.write'
          );
          select * into replay from app_private.learning_pdf_study_request
            where actor_identity_user_id=p_actor and idempotency_key=p_idempotency;
          if found then
            if replay.entitlement_id<>p_entitlement or replay.asset_id<>p_asset
               or replay.asset_version<>authorized.asset_version
               or replay.body_sha256<>p_body_sha256 then
              raise exception 'PDF study idempotency conflict' using errcode='23505';
            end if;
            return replay.result;
          end if;

          insert into app_private.learning_pdf_study_state(
            seller_organization_id,buyer_identity_user_id,entitlement_id,asset_id,asset_version
          ) values(
            authorized.seller_organization_id,authorized.buyer_identity_user_id,
            authorized.entitlement_id,authorized.asset_id,authorized.asset_version
          ) on conflict do nothing;
          select revision into strict current_revision
            from app_private.learning_pdf_study_state
            where seller_organization_id=authorized.seller_organization_id
              and buyer_identity_user_id=authorized.buyer_identity_user_id
              and entitlement_id=authorized.entitlement_id
              and asset_id=authorized.asset_id and asset_version=authorized.asset_version
            for update;
          if current_revision<>p_expected_revision then
            raise exception 'PDF study revision conflict' using errcode='40001';
          end if;

          action:=p_mutation->>'action';
          if action='annotation.upsert' then
            annotation:=p_mutation->'annotation';
            if (select array_agg(key order by key) from jsonb_object_keys(p_mutation) key)
                 <>array['action','annotation']::text[]
               or jsonb_typeof(annotation)<>'object'
               or (select array_agg(key order by key) from jsonb_object_keys(annotation) key)
                  <>array['color','id','page','points','tool','width']::text[]
               or annotation->>'tool' not in ('pen','highlight')
               or jsonb_typeof(annotation->'id')<>'string'
               or jsonb_typeof(annotation->'page')<>'number'
               or jsonb_typeof(annotation->'tool')<>'string'
               or jsonb_typeof(annotation->'color')<>'string'
               or jsonb_typeof(annotation->'width')<>'number'
               or annotation->>'color'!~'^#[0-9A-Fa-f]{6}$'
               or jsonb_typeof(annotation->'points')<>'array'
               or jsonb_array_length(annotation->'points') not between 2 and 1024 then
              raise exception 'invalid PDF annotation' using errcode='22023';
            end if;
            begin
              annotation_id:=(annotation->>'id')::uuid;
              page:=(annotation->>'page')::integer;
              invalid_points:=exists(
                select 1 from jsonb_array_elements(annotation->'points') point
                where jsonb_typeof(point)<>'object'
                   or (select array_agg(key order by key) from jsonb_object_keys(point) key)
                      <>array['x','y']::text[]
                   or jsonb_typeof(point->'x')<>'number' or jsonb_typeof(point->'y')<>'number'
                   or (point->>'x')::double precision not between 0 and 1
                   or (point->>'y')::double precision not between 0 and 1
              );
              if page not between 1 and authorized.page_count
                 or (annotation->>'width')::double precision not between 0.0005 and 0.05
                 or invalid_points then
                raise exception 'invalid PDF annotation values' using errcode='22023';
              end if;
            exception when invalid_text_representation or numeric_value_out_of_range then
              raise exception 'invalid PDF annotation values' using errcode='22023';
            end;
            insert into app_private.learning_pdf_annotation(
              seller_organization_id,buyer_identity_user_id,entitlement_id,asset_id,
              asset_version,id,page_number,tool,color,width,points
            ) values(
              authorized.seller_organization_id,authorized.buyer_identity_user_id,
              authorized.entitlement_id,authorized.asset_id,authorized.asset_version,
              annotation_id,page,annotation->>'tool',annotation->>'color',
              (annotation->>'width')::double precision,annotation->'points'
            ) on conflict(
              seller_organization_id,buyer_identity_user_id,entitlement_id,
              asset_id,asset_version,id
            ) do update set page_number=excluded.page_number,tool=excluded.tool,
              color=excluded.color,width=excluded.width,points=excluded.points,
              updated_at=clock_timestamp();
          elsif action='annotation.delete' then
            if (select array_agg(key order by key) from jsonb_object_keys(p_mutation) key)
                 <>array['action','id']::text[] then
              raise exception 'invalid PDF annotation delete' using errcode='22023';
            end if;
            begin annotation_id:=(p_mutation->>'id')::uuid;
            exception when invalid_text_representation then
              raise exception 'invalid PDF annotation id' using errcode='22023'; end;
            delete from app_private.learning_pdf_annotation where
              seller_organization_id=authorized.seller_organization_id
              and buyer_identity_user_id=authorized.buyer_identity_user_id
              and entitlement_id=authorized.entitlement_id
              and asset_id=authorized.asset_id and asset_version=authorized.asset_version
              and id=annotation_id;
          elsif action='bookmark.set' then
            if (select array_agg(key order by key) from jsonb_object_keys(p_mutation) key)
                 <>array['action','active','page']::text[]
               or jsonb_typeof(p_mutation->'active')<>'boolean'
               or jsonb_typeof(p_mutation->'page')<>'number' then
              raise exception 'invalid PDF bookmark' using errcode='22023';
            end if;
            begin page:=(p_mutation->>'page')::integer;
            exception when invalid_text_representation or numeric_value_out_of_range then
              raise exception 'invalid PDF bookmark page' using errcode='22023'; end;
            if page not between 1 and authorized.page_count then
              raise exception 'invalid PDF bookmark page' using errcode='22023';
            end if;
            if (p_mutation->>'active')::boolean then
              insert into app_private.learning_pdf_bookmark(
                seller_organization_id,buyer_identity_user_id,entitlement_id,
                asset_id,asset_version,page_number
              ) values(
                authorized.seller_organization_id,authorized.buyer_identity_user_id,
                authorized.entitlement_id,authorized.asset_id,authorized.asset_version,page
              ) on conflict do nothing;
            else
              delete from app_private.learning_pdf_bookmark where
                seller_organization_id=authorized.seller_organization_id
                and buyer_identity_user_id=authorized.buyer_identity_user_id
                and entitlement_id=authorized.entitlement_id
                and asset_id=authorized.asset_id and asset_version=authorized.asset_version
                and page_number=page;
            end if;
          else
            raise exception 'invalid PDF study action' using errcode='22023';
          end if;

          update app_private.learning_pdf_study_state set
            revision=revision+1,updated_at=clock_timestamp()
            where seller_organization_id=authorized.seller_organization_id
              and buyer_identity_user_id=authorized.buyer_identity_user_id
              and entitlement_id=authorized.entitlement_id
              and asset_id=authorized.asset_id and asset_version=authorized.asset_version;
          result:=app_private.project_pdf_study_state(
            authorized.seller_organization_id,authorized.buyer_identity_user_id,
            authorized.entitlement_id,authorized.asset_id,authorized.asset_version,
            authorized.page_count
          );
          insert into app_private.learning_pdf_study_request(
            actor_identity_user_id,idempotency_key,entitlement_id,asset_id,asset_version,
            body_sha256,request_id,result
          ) values(
            p_actor,p_idempotency,p_entitlement,p_asset,authorized.asset_version,
            p_body_sha256,p_request_id,result
          );
          return result;
        end $$
        """,
        "revoke all on app_private.learning_pdf_study_state from public,appsell_runtime",
        "revoke all on app_private.learning_pdf_annotation from public,appsell_runtime",
        "revoke all on app_private.learning_pdf_bookmark from public,appsell_runtime",
        "revoke all on app_private.learning_pdf_study_request from public,appsell_runtime",
        "revoke all on function app_private.authorize_pdf_study_asset(uuid,uuid,uuid,text) from public,appsell_runtime",
        "revoke all on function app_private.project_pdf_study_state(uuid,uuid,uuid,uuid,integer,integer) from public,appsell_runtime",
        "revoke all on function app_private.read_pdf_study_state(uuid,uuid,uuid) from public",
        "grant execute on function app_private.read_pdf_study_state(uuid,uuid,uuid) to appsell_runtime",
        "revoke all on function app_private.mutate_pdf_study_state(uuid,uuid,uuid,bigint,uuid,text,uuid,jsonb) from public",
        "grant execute on function app_private.mutate_pdf_study_state(uuid,uuid,uuid,bigint,uuid,text,uuid,jsonb) to appsell_runtime",
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition:=pg_get_functiondef('app_private.schema_ready_base_0016()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL}$old$,$new${_READY_TAIL_WITH_0033}$new$);
          if changed=definition then raise exception 'cannot extend 0016 readiness for 0033' using errcode='55000'; end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0017()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL}$old$,$new${_READY_TAIL_WITH_0033}$new$);
          if changed=definition then raise exception 'cannot extend 0017 readiness for 0033' using errcode='55000'; end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready()'::regprocedure);
          changed:=replace(definition,$old${_READY_ANCHOR}$old$,$new${_READY_ADDITION}$new$);
          if changed=definition then raise exception 'cannot extend schema readiness for 0033' using errcode='55000'; end if;
          execute changed;
        end $migration$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition:=pg_get_functiondef('app_private.schema_ready()'::regprocedure);
          changed:=replace(definition,$old${_READY_ADDITION}$old$,$new${_READY_ANCHOR}$new$);
          if changed=definition then raise exception 'cannot restore readiness from 0033' using errcode='55000'; end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0017()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL_WITH_0033}$old$,$new${_READY_TAIL}$new$);
          if changed=definition then raise exception 'cannot restore 0017 readiness from 0033' using errcode='55000'; end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0016()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL_WITH_0033}$old$,$new${_READY_TAIL}$new$);
          if changed=definition then raise exception 'cannot restore 0016 readiness from 0033' using errcode='55000'; end if;
          execute changed;
        end $migration$
        """,
        "drop function app_private.mutate_pdf_study_state(uuid,uuid,uuid,bigint,uuid,text,uuid,jsonb)",
        "drop function app_private.read_pdf_study_state(uuid,uuid,uuid)",
        "drop function app_private.project_pdf_study_state(uuid,uuid,uuid,uuid,integer,integer)",
        "drop function app_private.authorize_pdf_study_asset(uuid,uuid,uuid,text)",
        "drop table app_private.learning_pdf_study_request",
        "drop table app_private.learning_pdf_bookmark",
        "drop table app_private.learning_pdf_annotation",
        "drop table app_private.learning_pdf_study_state",
        _LEGACY_LIBRARY_CONTEXT,
    ]
    for statement in statements:
        op.execute(statement)
