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


def downgrade() -> None:
    op.execute("drop function app_private.read_learning_player_v2(uuid,uuid,uuid)")
    op.execute("drop trigger publication_lesson_asset_limit on public.publication_asset")
    op.execute("drop function app_private.enforce_publication_lesson_asset_limit()")
