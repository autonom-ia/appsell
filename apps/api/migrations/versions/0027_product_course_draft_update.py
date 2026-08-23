"""Allow product/course editorial saves before a seller creates an offer.

The seller editor has separate editorial and commercial steps.  The original
aggregate update function made the editorial step depend on an offer row, even
though ``create_product_course_draft`` intentionally creates only the product
and course.  This migration adds a narrow, idempotent product/course mutation
with the same tenant, membership, version and audit guarantees.
"""

from __future__ import annotations

from alembic import op

revision = "0027_product_course_draft_update"
down_revision = "0026_asset_worker_claim_filename"
branch_labels = None
depends_on = None

_SIGNATURE = (
    "app_private.update_product_course_draft("
    "uuid,uuid,uuid,text,uuid,text,text,text,bigint,bigint,uuid,text,uuid)"
)
_READY_TAIL = "'0025_seller_membership_role','0026_asset_worker_claim_filename')"
_READY_TAIL_WITH_0027 = (
    "'0025_seller_membership_role','0026_asset_worker_claim_filename',"
    "'0027_product_course_draft_update')"
)


_FUNCTION = """
create function app_private.update_product_course_draft(
  p_identity_user_id uuid,
  p_seller_organization_id uuid,
  p_catalog_product_id uuid,
  p_product_slug text,
  p_course_id uuid,
  p_product_title text,
  p_product_description text,
  p_course_title text,
  p_expected_product_version bigint,
  p_expected_course_version bigint,
  p_idempotency_key uuid,
  p_body_sha256 text,
  p_request_id uuid
) returns table (
  product_version bigint,
  course_version bigint,
  updated boolean
)
language plpgsql security definer
set search_path = pg_catalog, app_private, public
as $$
declare
  existing_request app_private.product_course_draft_update_request%rowtype;
  locked_product public.catalog_product%rowtype;
  locked_course public.course%rowtype;
  next_product_version bigint;
  next_course_version bigint;
  response jsonb;
begin
  if app_private.context_text('capability') <> 'catalog.draft.update'
     or app_private.context_uuid('identity_user_id') is distinct from
        p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from
        p_seller_organization_id then
    raise exception 'catalog product course update context is not authorized'
      using errcode = '28000';
  end if;

  if p_identity_user_id is null or p_seller_organization_id is null
     or p_catalog_product_id is null or p_course_id is null
     or p_idempotency_key is null or p_request_id is null
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
     or p_expected_product_version is null or p_expected_product_version < 1
     or p_expected_course_version is null or p_expected_course_version < 1
     or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid product course draft update request'
      using errcode = '22023';
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
    raise exception 'catalog product course owner is not authorized'
      using errcode = '28000';
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended(
      'appsell.catalog.mutation:' || p_seller_organization_id::text ||
      '/product-course-draft.update/' || p_idempotency_key::text,
      0
    )
  );
  select * into existing_request
  from app_private.product_course_draft_update_request request
  where request.seller_organization_id = p_seller_organization_id
    and request.idempotency_key = p_idempotency_key;
  if found then
    if existing_request.identity_user_id = p_identity_user_id
       and existing_request.body_sha256 = p_body_sha256
       and existing_request.catalog_product_id = p_catalog_product_id
       and existing_request.course_id = p_course_id then
      return query select
        (existing_request.result ->> 'product_version')::bigint,
        (existing_request.result ->> 'course_version')::bigint,
        false;
      return;
    end if;
    raise exception 'product course draft idempotency conflict'
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
    'course_version', next_course_version
  );
  insert into app_private.product_course_draft_update_request (
    seller_organization_id, idempotency_key, identity_user_id,
    body_sha256, catalog_product_id, course_id, result
  ) values (
    p_seller_organization_id, p_idempotency_key, p_identity_user_id,
    p_body_sha256, p_catalog_product_id, p_course_id, response
  );
  insert into public.audit_event (
    seller_organization_id, actor_identity_user_id, action, target_type,
    target_id, result, request_id, metadata
  ) values (
    p_seller_organization_id, p_identity_user_id,
    'catalog.product_course_draft_updated', 'catalog_product',
    p_catalog_product_id, 'succeeded', p_request_id,
    jsonb_build_object(
      'course_id', p_course_id,
      'product_version', next_product_version,
      'course_version', next_course_version
    )
  );
  return query select next_product_version, next_course_version, true;
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
            definition,
            $needle${_READY_TAIL}$needle$,
            $replacement${_READY_TAIL_WITH_0027}$replacement$
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
            $needle${_READY_TAIL}$needle$,
            $replacement${_READY_TAIL_WITH_0027}$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0017 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        """
        create table app_private.product_course_draft_update_request (
          seller_organization_id uuid not null,
          idempotency_key uuid not null,
          identity_user_id uuid not null references public.identity_user(id),
          body_sha256 text not null check (body_sha256 ~ '^[0-9a-f]{64}$'),
          catalog_product_id uuid not null,
          course_id uuid not null,
          result jsonb not null check (jsonb_typeof(result) = 'object'),
          created_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id, idempotency_key),
          constraint product_course_draft_update_product_fkey
            foreign key (seller_organization_id, catalog_product_id)
            references public.catalog_product (seller_organization_id, id),
          constraint product_course_draft_update_course_fkey
            foreign key (seller_organization_id, course_id)
            references public.course (seller_organization_id, id)
        )
        """,
        """
        create index product_course_draft_update_actor_idx
          on app_private.product_course_draft_update_request (
            identity_user_id, seller_organization_id, created_at desc
          )
        """,
        "revoke all on table app_private.product_course_draft_update_request from public, appsell_runtime",
        _FUNCTION,
        f"revoke all on function {_SIGNATURE} from public",
        f"grant execute on function {_SIGNATURE} to appsell_runtime",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        f"revoke all on function {_SIGNATURE} from public",
        f"drop function {_SIGNATURE}",
        "drop table app_private.product_course_draft_update_request",
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed := replace(
            definition,
            $needle${_READY_TAIL_WITH_0027}$needle$,
            $replacement${_READY_TAIL}$replacement$
          );
          if changed = definition then
            raise exception 'cannot restore 0017 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;

          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0016()'::regprocedure
          );
          changed := replace(
            definition,
            $needle${_READY_TAIL_WITH_0027}$needle$,
            $replacement${_READY_TAIL}$replacement$
          );
          if changed = definition then
            raise exception 'cannot restore 0016 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
    ]
    for statement in statements:
        op.execute(statement)
