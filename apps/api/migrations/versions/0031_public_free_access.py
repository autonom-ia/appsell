"""Publish an explicit free-access mode and grant public enrollments safely.

The public catalog already distinguishes a published product from a checkout
session.  This revision adds the missing, real-user path for a published draft
offer: a buyer can enroll without payment, with the same tenant and publication
checks used by the learning projection.
"""

from __future__ import annotations

from alembic import op

revision = "0031_public_free_access"
down_revision = "0030_product_pending_assets"
branch_labels = None
depends_on = None

_SIGNATURE = "app_private.grant_public_product_access(uuid,uuid,uuid,uuid,text,uuid)"
_READY_TAIL = (
    "'0025_seller_membership_role','0026_asset_worker_claim_filename',"
    "'0027_product_course_draft_update','0028_offer_activation_guard',"
    "'0029_product_draft_asset_access','0030_product_pending_assets')"
)
_READY_TAIL_WITH_0031 = (
    "'0025_seller_membership_role','0026_asset_worker_claim_filename',"
    "'0027_product_course_draft_update','0028_offer_activation_guard',"
    "'0029_product_draft_asset_access','0030_product_pending_assets',"
    "'0031_public_free_access')"
)
_READY_ANCHOR = (
    "select app_private.schema_ready_base_0017()\n"
    "            and to_regprocedure("
    "'app_private.list_product_draft_lesson_assets(uuid,uuid,uuid,uuid,uuid,uuid,text)'"
    ") is not null\n"
    "            and to_regprocedure("
    "'app_private.product_draft_has_pending_asset_upload(uuid,uuid,uuid,text)'"
    ") is not null"
)
_READY_ADDITION = f"""{_READY_ANCHOR}
            and to_regprocedure('{_SIGNATURE}') is not null"""

_PUBLIC_STOREFRONT = """
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
    'access_mode',case when offer.status='active' then 'paid' else 'free' end,
    'price',case when offer.status='active' and offer.unit_amount_minor > 0
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
    and product.status='published'
    and offer.status <> 'archived';

  return jsonb_build_object(
    'schema_version',1,
    'seller',jsonb_build_object(
      'id',seller.id,'slug',seller.slug,'name',seller.display_name
    ),
    'products',products
  );
end $$
"""

_LEGACY_PUBLIC_STOREFRONT = """
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
    and product.status='published'
    and offer.status <> 'archived';

  return jsonb_build_object(
    'schema_version',1,
    'seller',jsonb_build_object(
      'id',seller.id,'slug',seller.slug,'name',seller.display_name
    ),
    'products',products
  );
end $$
"""

_FREE_ACCESS_FUNCTION = """
create function app_private.grant_public_product_access(
  p_identity_user_id uuid,
  p_seller_organization_id uuid,
  p_catalog_product_id uuid,
  p_idempotency_key uuid,
  p_body_sha256 text,
  p_request_id uuid
) returns table(entitlement_id uuid, created boolean)
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare
  prior_request app_private.free_access_request%rowtype;
  locked_product public.catalog_product%rowtype;
  current_offer public.offer%rowtype;
  current_publication public.publication%rowtype;
  prior_entitlement public.buyer_entitlement%rowtype;
  next_entitlement uuid;
  was_created boolean;
begin
  if p_identity_user_id is null or p_seller_organization_id is null
     or p_catalog_product_id is null or p_idempotency_key is null
     or p_request_id is null
     or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$'
     or app_private.context_text('capability') is distinct from 'library.enroll'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is not null then
    raise exception 'public product access context is not authorized'
      using errcode='42501';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(
    'appsell.free-access:' || p_seller_organization_id::text || '/' ||
    p_idempotency_key::text, 0));
  select * into prior_request
  from app_private.free_access_request request
  where request.seller_organization_id=p_seller_organization_id
    and request.idempotency_key=p_idempotency_key
  for update;
  if found then
    if prior_request.buyer_identity_user_id=p_identity_user_id
       and prior_request.catalog_product_id=p_catalog_product_id
       and prior_request.body_sha256=p_body_sha256 then
      return query select prior_request.entitlement_id,false;
      return;
    end if;
    raise exception 'public product access idempotency conflict' using errcode='23505';
  end if;

  select * into locked_product
  from public.catalog_product product
  where product.seller_organization_id=p_seller_organization_id
    and product.id=p_catalog_product_id
  for share;
  if not found or locked_product.status <> 'published'
     or locked_product.published_snapshot_id is null then
    raise exception 'public product is unavailable' using errcode='28000';
  end if;

  select * into current_publication
  from public.publication publication
  where publication.seller_organization_id=p_seller_organization_id
    and publication.catalog_product_id=p_catalog_product_id
    and publication.id=locked_product.published_snapshot_id;
  if not found then
    raise exception 'public product publication is unavailable' using errcode='28000';
  end if;

  select * into current_offer
  from public.offer offer
  where offer.seller_organization_id=p_seller_organization_id
    and offer.catalog_product_id=p_catalog_product_id
  order by offer.version desc,offer.id desc
  limit 1
  for share;
  if not found or current_offer.status='archived' then
    raise exception 'public product offer is unavailable' using errcode='28000';
  end if;
  if current_offer.status='active' then
    raise exception 'paid offer requires checkout' using errcode='P4222';
  end if;
  if current_offer.status <> 'draft' then
    raise exception 'public product offer is unavailable' using errcode='28000';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(
    'appsell.free-access-subject:' || p_identity_user_id::text || '/' ||
    p_catalog_product_id::text, 0));
  select * into prior_entitlement
  from public.buyer_entitlement entitlement
  where entitlement.seller_organization_id=p_seller_organization_id
    and entitlement.buyer_identity_user_id=p_identity_user_id
    and entitlement.catalog_product_id=p_catalog_product_id
  for update;

  if found then
    next_entitlement := prior_entitlement.id;
    was_created := false;
    update public.buyer_entitlement
    set publication_id=current_publication.id,
        status='active',
        starts_at=clock_timestamp(),
        expires_at=null,
        updated_at=clock_timestamp()
    where seller_organization_id=p_seller_organization_id and id=next_entitlement;
  else
    next_entitlement := gen_random_uuid();
    was_created := true;
    insert into public.buyer_entitlement(
      seller_organization_id,id,buyer_identity_user_id,catalog_product_id,
      publication_id,reference_key,source,status
    ) values(
      p_seller_organization_id,next_entitlement,p_identity_user_id,p_catalog_product_id,
      current_publication.id,
      'free:' || p_identity_user_id::text || ':' || p_catalog_product_id::text,
      'free_access','active'
    );
  end if;

  insert into app_private.free_access_request(
    seller_organization_id,idempotency_key,buyer_identity_user_id,
    catalog_product_id,body_sha256,entitlement_id,created
  ) values(
    p_seller_organization_id,p_idempotency_key,p_identity_user_id,
    p_catalog_product_id,p_body_sha256,next_entitlement,was_created
  );
  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,'commerce.free_access_granted',
    'buyer_entitlement',next_entitlement,'succeeded',p_request_id,
    jsonb_build_object(
      'catalog_product_id',p_catalog_product_id,
      'publication_id',current_publication.id,
      'created',was_created
    )
  );
  return query select next_entitlement,was_created;
end
$$
"""


def upgrade() -> None:
    statements = [
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0016()'::regprocedure
          );
          changed := replace(
            definition, $needle${_READY_TAIL}$needle$,
            $replacement${_READY_TAIL_WITH_0031}$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0016 schema readiness revision'
              using errcode='55000';
          end if;
          execute changed;

          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed := replace(
            definition, $needle${_READY_TAIL}$needle$,
            $replacement${_READY_TAIL_WITH_0031}$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0017 schema readiness revision'
              using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """,
        """
        alter table public.buyer_entitlement
          drop constraint buyer_entitlement_source_check,
          add constraint buyer_entitlement_source_check
            check(source in ('demo_seed','manual','billing','demo_checkout','free_access'))
        """,
        """
        create table app_private.free_access_request (
          seller_organization_id uuid not null,
          idempotency_key uuid not null,
          buyer_identity_user_id uuid not null references public.identity_user(id),
          catalog_product_id uuid not null,
          body_sha256 text not null check (body_sha256 ~ '^[0-9a-f]{64}$'),
          entitlement_id uuid not null references public.buyer_entitlement(id),
          created boolean not null,
          created_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id,idempotency_key),
          constraint free_access_request_product_fkey
            foreign key (seller_organization_id,catalog_product_id)
            references public.catalog_product(seller_organization_id,id)
        )
        """,
        "revoke all on table app_private.free_access_request from public,appsell_runtime",
        _FREE_ACCESS_FUNCTION,
        f"revoke all on function {_SIGNATURE} from public",
        f"grant execute on function {_SIGNATURE} to appsell_runtime",
        _PUBLIC_STOREFRONT,
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef('app_private.schema_ready()'::regprocedure);
          changed := replace(
            definition,
            $needle${_READY_ANCHOR}$needle$,
            $replacement${_READY_ADDITION}$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend schema readiness for 0031'
              using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        """
        do $migration$
        begin
          if exists (
            select 1 from public.buyer_entitlement where source='free_access'
          ) or exists (
            select 1 from app_private.free_access_request
          ) then
            raise exception
              'cannot downgrade 0031_public_free_access: free-access records exist; use a forward migration'
              using errcode='55000';
          end if;
        end $migration$
        """,
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef('app_private.schema_ready()'::regprocedure);
          changed := replace(
            definition,
            $needle${_READY_ADDITION}$needle$,
            $replacement${_READY_ANCHOR}$replacement$
          );
          if changed = definition then
            raise exception 'cannot restore schema readiness for 0031'
              using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """,
        f"revoke all on function {_SIGNATURE} from public",
        f"drop function {_SIGNATURE}",
        "drop table app_private.free_access_request",
        "alter table public.buyer_entitlement drop constraint buyer_entitlement_source_check",
        "alter table public.buyer_entitlement add constraint buyer_entitlement_source_check check(source in ('demo_seed','manual','billing','demo_checkout'))",
        _LEGACY_PUBLIC_STOREFRONT,
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed := replace(
            definition, $needle${_READY_TAIL_WITH_0031}$needle$,
            $replacement${_READY_TAIL}$replacement$
          );
          if changed = definition then
            raise exception 'cannot restore 0017 schema readiness revision'
              using errcode='55000';
          end if;
          execute changed;

          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0016()'::regprocedure
          );
          changed := replace(
            definition, $needle${_READY_TAIL_WITH_0031}$needle$,
            $replacement${_READY_TAIL}$replacement$
          );
          if changed = definition then
            raise exception 'cannot restore 0016 schema readiness revision'
              using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """,
    ]
    for statement in statements:
        op.execute(statement)
