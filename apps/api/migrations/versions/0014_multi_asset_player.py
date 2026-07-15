"""Expose every protected asset attached to the selected lesson.

Revision ID: 0014_multi_asset_player
Revises: 0013_private_media
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_multi_asset_player"
down_revision: str | None = "0013_private_media"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema_ready_sql(expected_revision: str, *, include_multi_asset: bool) -> str:
    multi_asset_contract = ""
    if include_multi_asset:
        multi_asset_contract = """
            and to_regprocedure(
              'app_private.read_learning_player_v2(uuid,uuid,uuid)'
            ) is not null
            and exists(select 1 from pg_catalog.pg_trigger
              where tgrelid='public.publication_asset'::regclass
                and tgname='publication_lesson_asset_limit' and not tgisinternal
                and tgenabled='O')
        """
    # Both inputs are migration-owned constants, never request data.
    return f"""
        create or replace function app_private.schema_ready() returns boolean
        language sql stable security definer set search_path=pg_catalog,app_private as $$
          select to_regclass('public.identity_user') is not null
            and to_regclass('public.platform_grant') is not null
            and to_regclass('public.superadmin_bootstrap') is not null
            and to_regclass('public.seller_organization') is not null
            and to_regclass('public.seller_membership') is not null
            and to_regclass('public.audit_event') is not null
            and to_regclass('public.catalog_product') is not null
            and to_regclass('public.course') is not null
            and to_regclass('public.product_course') is not null
            and to_regclass('public.offer') is not null
            and to_regclass('public.publication') is not null
            and to_regclass('public.course_module') is not null
            and to_regclass('public.lesson') is not null
            and to_regclass('public.buyer_entitlement') is not null
            and to_regclass('public.learning_asset') is not null
            and to_regclass('public.publication_course') is not null
            and to_regclass('public.publication_lesson') is not null
            and to_regclass('public.learning_enrollment') is not null
            and to_regclass('public.lesson_progress') is not null
            and to_regclass('public.course_learning_state') is not null
            and to_regclass('public.asset_record') is not null
            and to_regclass('public.asset_blob') is not null
            and to_regclass('public.asset_version') is not null
            and to_regclass('public.lesson_asset_draft') is not null
            and to_regclass('public.publication_asset') is not null
            and to_regclass('public.checkout_integration') is not null
            and to_regclass('public.webhook_endpoint') is not null
            and to_regclass('public.external_product_mapping') is not null
            and to_regclass('public.webhook_event') is not null
            and to_regclass('app_private.outbox_event') is not null
            and to_regclass('app_private.bff_request_replay') is not null
            and to_regclass('app_private.seller_onboarding_request') is not null
            and to_regclass('app_private.product_draft_request') is not null
            and to_regclass('app_private.catalog_mutation_request') is not null
            and to_regclass('app_private.curriculum_mutation_request') is not null
            and to_regclass('app_private.demo_tenant_registry') is not null
            and to_regclass('app_private.learning_progress_request') is not null
            and to_regclass('app_private.asset_upload') is not null
            and to_regclass('app_private.asset_verification_job') is not null
            and to_regclass('app_private.asset_storage_cleanup_job') is not null
            and to_regclass('app_private.asset_mutation_request') is not null
            and to_regclass('app_private.asset_worker_identity') is not null
            and to_regclass('app_private.demo_inspection_ticket') is not null
            and to_regprocedure('app_private.read_buyer_products(uuid)') is not null
            and to_regprocedure(
              'app_private.read_buyer_seller_storefront(uuid,uuid)'
            ) is not null
            and to_regprocedure(
              'app_private.start_demo_inspection(uuid,text,text,integer,text,uuid)'
            ) is not null
            and to_regprocedure(
              'app_private.read_demo_inspection(uuid,text,uuid,text,uuid)'
            ) is not null
            and to_regprocedure(
              'app_private.revoke_demo_inspection(uuid,text,uuid,text,text,uuid)'
            ) is not null
            and to_regprocedure('app_private.read_public_storefront(text)') is not null
            and to_regprocedure(
              'app_private.resolve_webhook_endpoint(text)'
            ) is not null
            and to_regprocedure(
              'app_private.accept_checkout_webhook(uuid,uuid,text,text,bigint,text,'
              'smallint,text,integer,text,text,smallint,text,smallint,timestamptz,'
              'bigint,uuid)'
            ) is not null
            {multi_asset_contract}
            and exists(select 1 from pg_catalog.pg_trigger
              where tgrelid='public.webhook_event'::regclass
                and tgname='webhook_event_immutable' and not tgisinternal
                and tgenabled='O')
            and not exists(
              select 1 from unnest(array[
                'public.checkout_integration'::regclass,
                'public.webhook_endpoint'::regclass,
                'public.external_product_mapping'::regclass,
                'public.webhook_event'::regclass
              ]) relation(oid)
              join pg_catalog.pg_class class on class.oid=relation.oid
              where not class.relrowsecurity or not class.relforcerowsecurity
            )
            and exists(select 1 from public.alembic_version
              where version_num='{expected_revision}')
            and exists(select 1 from app_private.context_signing_key where singleton)
            and exists(select 1 from app_private.bootstrap_trust_policy where singleton)
        $$
    """  # noqa: S608


def upgrade() -> None:
    # Block concurrent publication projection while validating the legacy state and
    # installing the row trigger. Alembic keeps this lock until the migration commits.
    op.execute("lock table public.publication_asset in share row exclusive mode")
    op.execute(
        """
        do $$
        begin
          if exists(
            select 1 from public.publication_asset
            group by seller_organization_id,publication_id,lesson_id
            having count(*)>100
          ) then
            raise exception 'publication lesson exceeds the 100 asset contract'
              using errcode='23514';
          end if;
        end $$
        """
    )
    op.execute(
        """
        create function app_private.enforce_publication_lesson_asset_limit()
        returns trigger language plpgsql security definer
        set search_path=pg_catalog,public as $$
        begin
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.publication.asset-limit:'||new.seller_organization_id::text||'/'||
            new.publication_id::text||'/'||new.lesson_id::text,0));
          if (select count(*) from public.publication_asset asset
              where asset.seller_organization_id=new.seller_organization_id
                and asset.publication_id=new.publication_id
                and asset.lesson_id=new.lesson_id)>=100 then
            raise exception 'publication lesson exceeds the 100 asset contract'
              using errcode='23514';
          end if;
          return new;
        end $$
        """
    )
    op.execute(
        "revoke all on function app_private.enforce_publication_lesson_asset_limit() "
        "from public,appsell_runtime"
    )
    op.execute(
        """
        create trigger publication_lesson_asset_limit
        before insert on public.publication_asset for each row
        execute function app_private.enforce_publication_lesson_asset_limit()
        """
    )
    op.execute(
        """
        create function app_private.read_learning_player_v2(
          p_actor uuid,p_entitlement uuid,p_lesson uuid default null
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare player jsonb; assets jsonb;
        begin
          player:=app_private.read_learning_player(p_actor,p_entitlement,p_lesson);
          select coalesce(jsonb_agg(jsonb_build_object(
            'id',asset.asset_id,'title',asset.title,'media_type',asset.media_type
          ) order by asset.position,asset.asset_id),'[]'::jsonb) into assets
          from public.publication_asset asset
          where asset.seller_organization_id=(player->'seller'->>'id')::uuid
            and asset.catalog_product_id=(player->'product'->>'id')::uuid
            and asset.publication_id=(player->'publication'->>'id')::uuid
            and asset.lesson_id=(player->>'selected_lesson_id')::uuid;
          return player || jsonb_build_object('selected_assets',assets);
        end $$
        """
    )
    op.execute(
        "revoke all on function app_private.read_learning_player_v2(uuid,uuid,uuid) from public"
    )
    op.execute(
        "grant execute on function app_private.read_learning_player_v2(uuid,uuid,uuid) "
        "to appsell_runtime"
    )
    op.execute(_schema_ready_sql(revision, include_multi_asset=True))


def downgrade() -> None:
    op.execute("drop function app_private.read_learning_player_v2(uuid,uuid,uuid)")
    op.execute("drop trigger publication_lesson_asset_limit on public.publication_asset")
    op.execute("drop function app_private.enforce_publication_lesson_asset_limit()")
    if down_revision is None:
        raise RuntimeError("multi-asset downgrade requires its parent revision")
    op.execute(_schema_ready_sql(down_revision, include_multi_asset=False))
