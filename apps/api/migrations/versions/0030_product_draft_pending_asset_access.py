"""Read product-level pending asset state through the protected projection.

The draft detail projection also reports whether an upload is still being
verified.  ``asset_upload`` is intentionally not readable by the runtime
role, so expose only that scoped boolean through a security-definer function.
"""

from __future__ import annotations

from alembic import op

revision = "0030_product_pending_assets"
down_revision = "0029_product_draft_asset_access"
branch_labels = None
depends_on = None

_SIGNATURE = "app_private.product_draft_has_pending_asset_upload(uuid,uuid,uuid,text)"
_READY_TAIL = (
    "'0025_seller_membership_role','0026_asset_worker_claim_filename',"
    "'0027_product_course_draft_update','0028_offer_activation_guard',"
    "'0029_product_draft_asset_access')"
)
_READY_TAIL_WITH_0030 = (
    "'0025_seller_membership_role','0026_asset_worker_claim_filename',"
    "'0027_product_course_draft_update','0028_offer_activation_guard',"
    "'0029_product_draft_asset_access','0030_product_pending_assets')"
)
_READY_ANCHOR = (
    "select app_private.schema_ready_base_0017()\n"
    "            and to_regprocedure("
    "'app_private.list_product_draft_lesson_assets(uuid,uuid,uuid,uuid,uuid,uuid,text)'"
    ") is not null"
)
_READY_ADDITION = f"""{_READY_ANCHOR}
            and to_regprocedure('{_SIGNATURE}') is not null"""

_FUNCTION = """
create function app_private.product_draft_has_pending_asset_upload(
  p_actor uuid,
  p_seller uuid,
  p_product uuid,
  p_capability text
) returns boolean
language plpgsql security definer
set search_path = pg_catalog, app_private, public, pg_temp
as $$
begin
  if p_actor is null or p_seller is null or p_product is null
     or p_capability is null
     or p_capability not in ('catalog.draft.read','catalog.draft.preview')
     or app_private.context_text('capability') is distinct from p_capability
     or app_private.context_uuid('identity_user_id') is distinct from p_actor
     or app_private.context_uuid('seller_organization_id') is distinct from p_seller then
    raise exception 'product draft pending upload context is not authorized'
      using errcode = '42501';
  end if;

  perform 1
  from public.seller_organization organization
  join public.seller_membership membership
    on membership.seller_organization_id = organization.id
  where organization.id = p_seller
    and organization.status = 'active'
    and membership.identity_user_id = p_actor
    and membership.role in ('seller_owner','seller_admin','seller_editor')
    and membership.status = 'active';
  if not found then
    raise exception 'product draft pending upload actor is not authorized'
      using errcode = '28000';
  end if;

  perform 1
  from public.catalog_product product
  where product.seller_organization_id = p_seller
    and product.id = p_product;
  if not found then
    raise exception 'product draft pending upload product is unavailable'
      using errcode = '28000';
  end if;

  return exists(
    select 1
    from app_private.asset_upload pending_upload
    where pending_upload.seller_organization_id = p_seller
      and pending_upload.catalog_product_id = p_product
      and (
        pending_upload.status in ('uploaded', 'verifying')
        or (
          pending_upload.status = 'pending'
          and pending_upload.expires_at > clock_timestamp()
        )
      )
  );
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
            $replacement${_READY_TAIL_WITH_0030}$replacement$
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
            definition, $needle${_READY_TAIL}$needle$,
            $replacement${_READY_TAIL_WITH_0030}$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0017 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        _FUNCTION,
        f"revoke all on function {_SIGNATURE} from public",
        f"grant execute on function {_SIGNATURE} to appsell_runtime",
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
            raise exception 'cannot extend schema readiness for 0030'
              using errcode = '55000';
          end if;
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
          definition := pg_get_functiondef('app_private.schema_ready()'::regprocedure);
          changed := replace(
            definition,
            $needle${_READY_ADDITION}$needle$,
            $replacement${_READY_ANCHOR}$replacement$
          );
          if changed = definition then
            raise exception 'cannot restore schema readiness for 0030'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        f"revoke all on function {_SIGNATURE} from public",
        f"drop function {_SIGNATURE}",
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed := replace(
            definition, $needle${_READY_TAIL_WITH_0030}$needle$,
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
            definition, $needle${_READY_TAIL_WITH_0030}$needle$,
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
