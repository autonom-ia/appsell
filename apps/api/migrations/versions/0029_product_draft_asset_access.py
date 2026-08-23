"""Read draft materials through the protected catalog projection.

The product editor reads curriculum and its materials in one projection.  The
asset tables intentionally deny direct access to ``appsell_runtime``; this
function keeps that boundary and exposes only the material metadata needed by
the seller's draft/preview projection after the normal tenant and membership
checks.
"""

from __future__ import annotations

from alembic import op

revision = "0029_product_draft_asset_access"
down_revision = "0028_offer_activation_guard"
branch_labels = None
depends_on = None

_SIGNATURE = "app_private.list_product_draft_lesson_assets(uuid,uuid,uuid,uuid,uuid,uuid,text)"
_READY_TAIL = (
    "'0025_seller_membership_role','0026_asset_worker_claim_filename',"
    "'0027_product_course_draft_update','0028_offer_activation_guard')"
)
_READY_TAIL_WITH_0029 = (
    "'0025_seller_membership_role','0026_asset_worker_claim_filename',"
    "'0027_product_course_draft_update','0028_offer_activation_guard',"
    "'0029_product_draft_asset_access')"
)
_READY_ANCHOR = "select app_private.schema_ready_base_0017()"
_READY_ADDITION = f"""select app_private.schema_ready_base_0017()
            and to_regprocedure('{_SIGNATURE}') is not null"""

_FUNCTION = """
create function app_private.list_product_draft_lesson_assets(
  p_actor uuid,
  p_seller uuid,
  p_product uuid,
  p_course uuid,
  p_module uuid,
  p_lesson uuid,
  p_capability text
) returns jsonb
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp
as $$
declare result jsonb;
begin
  if p_capability is null
     or p_capability not in ('catalog.draft.read','catalog.draft.preview') then
    raise exception 'product draft asset context is not authorized'
      using errcode='42501';
  end if;

  perform app_private.lock_curriculum_access(
    p_actor,p_seller,p_product,p_course,p_capability
  );
  perform 1
  from public.lesson lesson
  where lesson.seller_organization_id=p_seller
    and lesson.course_id=p_course and lesson.module_id=p_module
    and lesson.id=p_lesson and lesson.status='active';
  if not found then
    raise exception 'product draft asset lesson is unavailable' using errcode='28000';
  end if;

  select coalesce(jsonb_agg(jsonb_build_object(
    'id',draft.asset_id,'version',draft.asset_version,
    'title',asset.title,'file_name',blob.file_name,
    'media_type',blob.media_type,'byte_size',blob.byte_size,
    'sha256',blob.sha256,'position',draft.position,
    'content_role',draft.content_role,
    'download_allowed',draft.download_allowed
  ) order by draft.position,draft.asset_id),'[]'::jsonb)
  into result
  from public.lesson_asset_draft draft
  join public.asset_record asset
    on asset.seller_organization_id=draft.seller_organization_id
   and asset.id=draft.asset_id and asset.status='active'
  join public.asset_blob blob
    on blob.seller_organization_id=draft.seller_organization_id
   and blob.id=draft.blob_id
  where draft.seller_organization_id=p_seller
    and draft.catalog_product_id=p_product and draft.course_id=p_course
    and draft.module_id=p_module and draft.lesson_id=p_lesson
    and draft.status='active';
  return result;
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
            $replacement${_READY_TAIL_WITH_0029}$replacement$
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
            $replacement${_READY_TAIL_WITH_0029}$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0017 schema readiness revision'
              using errcode='55000';
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
            raise exception 'cannot extend schema readiness for 0029'
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
            raise exception 'cannot restore schema readiness for 0029'
              using errcode='55000';
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
            definition, $needle${_READY_TAIL_WITH_0029}$needle$,
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
            definition, $needle${_READY_TAIL_WITH_0029}$needle$,
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
