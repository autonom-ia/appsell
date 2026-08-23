"""Require a published catalog before an offer can become active.

The seller UI already keeps activation behind catalog publication.  The domain
function must enforce the same invariant for direct callers and retries so an
active offer can never strand a product in a state that publication rejects.
"""

from __future__ import annotations

from alembic import op

revision = "0028_offer_activation_guard"
down_revision = "0027_product_course_draft_update"
branch_labels = None
depends_on = None

_SIGNATURE = (
    "app_private.transition_offer_status(uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid)"
)
_READY_TAIL = (
    "'0025_seller_membership_role','0026_asset_worker_claim_filename',"
    "'0027_product_course_draft_update')"
)
_READY_TAIL_WITH_0028 = (
    "'0025_seller_membership_role','0026_asset_worker_claim_filename',"
    "'0027_product_course_draft_update','0028_offer_activation_guard')"
)

_TRANSITION_FUNCTION = """
create or replace function app_private.transition_offer_status(
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
       or (p_expected_offer_status = 'active' and p_offer_status = 'draft')
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
  if p_offer_status = 'active' and not exists (
    select 1
    from public.catalog_product product
    join public.publication publication
      on publication.seller_organization_id = product.seller_organization_id
     and publication.catalog_product_id = product.id
     and publication.id = product.published_snapshot_id
    where product.seller_organization_id = p_seller_organization_id
      and product.id = p_catalog_product_id
      and product.status = 'published'
  ) then
    raise exception 'active offer requires a published catalog'
      using errcode = '55000';
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
            $replacement${_READY_TAIL_WITH_0028}$replacement$
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
            $replacement${_READY_TAIL_WITH_0028}$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0017 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        _TRANSITION_FUNCTION,
        f"revoke all on function {_SIGNATURE} from public",
        f"grant execute on function {_SIGNATURE} to appsell_runtime",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    # Restore the pre-0028 function body while keeping the 0027 migration
    # installed.  The previous function is deliberately reproduced here so a
    # downgrade does not silently leave a weaker readiness contract behind.
    original = _TRANSITION_FUNCTION.replace(
        "  if p_offer_status = 'active' and not exists (\n"
        "    select 1\n"
        "    from public.catalog_product product\n"
        "    join public.publication publication\n"
        "      on publication.seller_organization_id = product.seller_organization_id\n"
        "     and publication.catalog_product_id = product.id\n"
        "     and publication.id = product.published_snapshot_id\n"
        "    where product.seller_organization_id = p_seller_organization_id\n"
        "      and product.id = p_catalog_product_id\n"
        "      and product.status = 'published'\n"
        "  ) then\n"
        "    raise exception 'active offer requires a published catalog'\n"
        "      using errcode = '55000';\n"
        "  end if;\n",
        "",
    )
    if original == _TRANSITION_FUNCTION:
        raise RuntimeError("failed to restore the pre-0028 offer status function")
    statements = [
        original,
        f"revoke all on function {_SIGNATURE} from public",
        f"grant execute on function {_SIGNATURE} to appsell_runtime",
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed := replace(
            definition,
            $needle${_READY_TAIL_WITH_0028}$needle$,
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
            $needle${_READY_TAIL_WITH_0028}$needle$,
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
