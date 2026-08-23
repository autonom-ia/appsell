"""Allow the real platform superadmin to administer every seller catalog.

Seller memberships remain the authority for seller staff.  The platform
superadmin uses an explicit platform grant instead of a synthetic membership,
and every catalog mutation keeps the real actor identity in the audit event.
"""

from __future__ import annotations

from alembic import op

revision = "0032_platform_superadmin_catalog"
down_revision = "0031_public_free_access"
branch_labels = None
depends_on = None

_PLATFORM_ALLOWED_SIGNATURE = (
    "app_private.platform_superadmin_catalog_allowed(uuid,uuid,text)"
)
_LIST_SIGNATURE = "app_private.list_accessible_sellers(uuid)"
_READY_TAIL = (
    "'0025_seller_membership_role','0026_asset_worker_claim_filename',"
    "'0027_product_course_draft_update','0028_offer_activation_guard',"
    "'0029_product_draft_asset_access','0030_product_pending_assets',"
    "'0031_public_free_access')"
)
_READY_TAIL_WITH_0032 = (
    "'0025_seller_membership_role','0026_asset_worker_claim_filename',"
    "'0027_product_course_draft_update','0028_offer_activation_guard',"
    "'0029_product_draft_asset_access','0030_product_pending_assets',"
    "'0031_public_free_access','0032_platform_superadmin_catalog')"
)
_READY_ANCHOR = (
    "and to_regprocedure("
    "'app_private.grant_public_product_access(uuid,uuid,uuid,uuid,text,uuid)'"
    ") is not null"
)
_READY_ADDITION = f"""{_READY_ANCHOR}
            and to_regprocedure('{_PLATFORM_ALLOWED_SIGNATURE}') is not null
            and to_regprocedure('{_LIST_SIGNATURE}') is not null"""

_PLATFORM_ALLOWED = """
create function app_private.platform_superadmin_catalog_allowed(
  p_actor uuid,
  p_seller uuid,
  p_capability text
) returns boolean
language sql stable security definer
set search_path=pg_catalog,app_private,public,pg_temp
as $$
  select coalesce(
    p_actor is not null
    and p_seller is not null
    and p_capability is not null
    and p_capability like 'catalog.%'
    and app_private.context_uuid('identity_user_id') = p_actor
    and app_private.context_uuid('seller_organization_id') = p_seller
    and app_private.context_text('capability') = p_capability
    and exists(
      select 1 from public.seller_organization organization
      where organization.id=p_seller and organization.status='active'
    )
    and exists(
      select 1 from public.platform_grant platform
      where platform.identity_user_id=p_actor
        and platform.role='platform_superadmin'
        and platform.status='active'
    ),
    false
  )
$$
"""

_LIST_ACCESSIBLE = """
create function app_private.list_accessible_sellers(p_actor uuid)
returns table(id uuid,slug text,display_name text,role text)
language plpgsql stable security definer
set search_path=pg_catalog,app_private,public,pg_temp
as $$
begin
  if p_actor is null
     or app_private.context_uuid('identity_user_id') is distinct from p_actor
     or app_private.context_uuid('seller_organization_id') is not null
     or app_private.context_text('capability') is distinct from 'seller.list' then
    raise exception 'seller list context is not authorized' using errcode='42501';
  end if;

  if exists(
    select 1 from public.platform_grant platform
    where platform.identity_user_id=p_actor
      and platform.role='platform_superadmin'
      and platform.status='active'
  ) then
    return query
      select organization.id,organization.slug,organization.display_name,
             'platform_superadmin'::text
      from public.seller_organization organization
      where organization.status='active'
      order by organization.created_at,organization.id;
    return;
  end if;

  return query
    select organization.id,organization.slug,organization.display_name,membership.role
    from public.seller_organization organization
    join public.seller_membership membership
      on membership.seller_organization_id=organization.id
    where membership.identity_user_id=p_actor
      and membership.status='active'
      and organization.status='active'
    order by organization.created_at,organization.id;
end
$$
"""

_CATALOG_DRAFT_READ_ALLOWED = """
create or replace function app_private.catalog_draft_read_allowed(
  p_seller_organization_id uuid
) returns boolean
language sql stable security definer
set search_path=pg_catalog,app_private,public
as $$
  select p_seller_organization_id is not null
    and app_private.context_text('capability') in (
      'catalog.draft.read','catalog.draft.preview'
    )
    and app_private.context_uuid('seller_organization_id')=p_seller_organization_id
    and (
      exists(
        select 1 from public.seller_organization organization
        join public.seller_membership membership
          on membership.seller_organization_id=organization.id
        where organization.id=p_seller_organization_id
          and organization.status='active'
          and membership.identity_user_id=app_private.context_uuid('identity_user_id')
          and membership.role='seller_owner'
          and membership.status='active'
      )
      or app_private.platform_superadmin_catalog_allowed(
        app_private.context_uuid('identity_user_id'),
        p_seller_organization_id,
        app_private.context_text('capability')
      )
    )
$$
"""

_CATALOG_OWNER_ACTION_ALLOWED = """
create or replace function app_private.catalog_owner_action_allowed(
  p_seller_organization_id uuid,
  p_capability text
) returns boolean
language sql stable security definer
set search_path=pg_catalog,app_private,public
as $$
  select p_seller_organization_id is not null
    and p_capability is not null
    and app_private.context_text('capability')=p_capability
    and app_private.context_uuid('seller_organization_id')=p_seller_organization_id
    and (
      exists(
        select 1 from public.seller_organization organization
        join public.seller_membership membership
          on membership.seller_organization_id=organization.id
        where organization.id=p_seller_organization_id
          and organization.status='active'
          and membership.identity_user_id=app_private.context_uuid('identity_user_id')
          and membership.role='seller_owner'
          and membership.status='active'
      )
      or app_private.platform_superadmin_catalog_allowed(
        app_private.context_uuid('identity_user_id'),p_seller_organization_id,p_capability
      )
    )
$$
"""

_CURRICULUM_ACTION_ALLOWED = """
create or replace function app_private.curriculum_action_allowed(
  p_seller_organization_id uuid,p_course_id uuid,p_capability text
) returns boolean
language sql stable security definer
set search_path=pg_catalog,app_private,public
as $$
  select p_seller_organization_id is not null and p_course_id is not null
    and app_private.context_text('capability')=p_capability
    and app_private.context_uuid('seller_organization_id')=p_seller_organization_id
    and (
      exists(
        select 1 from public.seller_organization organization
        join public.seller_membership membership
          on membership.seller_organization_id=organization.id
        where organization.id=p_seller_organization_id
          and organization.status='active'
          and membership.identity_user_id=app_private.context_uuid('identity_user_id')
          and membership.role in ('seller_owner','seller_admin','seller_editor')
          and membership.status='active'
      )
      or app_private.platform_superadmin_catalog_allowed(
        app_private.context_uuid('identity_user_id'),p_seller_organization_id,p_capability
      )
    )
    and exists(
      select 1 from public.course candidate
      where candidate.seller_organization_id=p_seller_organization_id
        and candidate.id=p_course_id and candidate.status<>'archived'
    )
$$
"""

_LOCK_CURRICULUM_ACCESS = """
create or replace function app_private.lock_curriculum_access(
  p_identity_user_id uuid,p_seller_organization_id uuid,
  p_catalog_product_id uuid,p_course_id uuid,p_capability text
) returns void
language plpgsql security definer
set search_path=pg_catalog,app_private,public
as $$
begin
  if app_private.context_text('capability')<>p_capability
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from
        p_seller_organization_id then
    raise exception 'curriculum context is not authorized' using errcode='28000';
  end if;
  if not (
    exists(
      select 1 from public.seller_organization organization
      join public.seller_membership membership
        on membership.seller_organization_id=organization.id
      where organization.id=p_seller_organization_id
        and organization.status='active'
        and membership.identity_user_id=p_identity_user_id
        and membership.role in ('seller_owner','seller_admin','seller_editor')
        and membership.status='active'
    )
    or app_private.platform_superadmin_catalog_allowed(
      p_identity_user_id,p_seller_organization_id,p_capability
    )
  ) then
    raise exception 'curriculum actor is not authorized' using errcode='28000';
  end if;
  perform 1 from public.product_course linked
  join public.course candidate
    on candidate.seller_organization_id=linked.seller_organization_id
   and candidate.id=linked.course_id
  where linked.seller_organization_id=p_seller_organization_id
    and linked.catalog_product_id=p_catalog_product_id
    and linked.course_id=p_course_id and candidate.status<>'archived'
  for share of linked;
  if not found then
    raise exception 'curriculum course is unavailable' using errcode='28000';
  end if;
end
$$
"""

_PENDING_ASSET_ACCESS = """
create or replace function app_private.product_draft_has_pending_asset_upload(
  p_actor uuid,p_seller uuid,p_product uuid,p_capability text
) returns boolean
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp
as $$
begin
  if p_actor is null or p_seller is null or p_product is null
     or p_capability not in ('catalog.draft.read','catalog.draft.preview')
     or app_private.context_text('capability') is distinct from p_capability
     or app_private.context_uuid('identity_user_id') is distinct from p_actor
     or app_private.context_uuid('seller_organization_id') is distinct from p_seller then
    raise exception 'product draft pending upload context is not authorized'
      using errcode='42501';
  end if;
  if not (
    exists(
      select 1 from public.seller_organization organization
      join public.seller_membership membership
        on membership.seller_organization_id=organization.id
      where organization.id=p_seller and organization.status='active'
        and membership.identity_user_id=p_actor
        and membership.role in ('seller_owner','seller_admin','seller_editor')
        and membership.status='active'
    )
    or app_private.platform_superadmin_catalog_allowed(p_actor,p_seller,p_capability)
  ) then
    raise exception 'product draft pending upload actor is not authorized'
      using errcode='28000';
  end if;
  perform 1 from public.catalog_product product
  where product.seller_organization_id=p_seller and product.id=p_product;
  if not found then
    raise exception 'product draft pending upload product is unavailable'
      using errcode='28000';
  end if;
  return exists(
    select 1 from app_private.asset_upload pending_upload
    where pending_upload.seller_organization_id=p_seller
      and pending_upload.catalog_product_id=p_product
      and (
        pending_upload.status in ('uploaded','verifying')
        or (pending_upload.status='pending' and pending_upload.expires_at>clock_timestamp())
      )
  );
end
$$
"""

_LEGACY_CATALOG_DRAFT_READ_ALLOWED = """
create or replace function app_private.catalog_draft_read_allowed(
  p_seller_organization_id uuid
) returns boolean
language sql stable security definer
set search_path=pg_catalog,app_private,public
as $$
  select p_seller_organization_id is not null
    and app_private.context_text('capability') in (
      'catalog.draft.read','catalog.draft.preview'
    )
    and app_private.context_uuid('seller_organization_id')=p_seller_organization_id
    and exists(
      select 1 from public.seller_organization organization
      join public.seller_membership membership
        on membership.seller_organization_id=organization.id
      where organization.id=p_seller_organization_id
        and organization.status='active'
        and membership.identity_user_id=app_private.context_uuid('identity_user_id')
        and membership.role='seller_owner'
        and membership.status='active'
    )
$$
"""

_LEGACY_CATALOG_OWNER_ACTION_ALLOWED = """
create or replace function app_private.catalog_owner_action_allowed(
  p_seller_organization_id uuid,p_capability text
) returns boolean
language sql stable security definer
set search_path=pg_catalog,app_private,public
as $$
  select p_seller_organization_id is not null
    and p_capability is not null
    and app_private.context_text('capability')=p_capability
    and app_private.context_uuid('seller_organization_id')=p_seller_organization_id
    and exists(
      select 1 from public.seller_organization organization
      join public.seller_membership membership
        on membership.seller_organization_id=organization.id
      where organization.id=p_seller_organization_id
        and organization.status='active'
        and membership.identity_user_id=app_private.context_uuid('identity_user_id')
        and membership.role='seller_owner'
        and membership.status='active'
    )
$$
"""

_LEGACY_CURRICULUM_ACTION_ALLOWED = """
create or replace function app_private.curriculum_action_allowed(
  p_seller_organization_id uuid,p_course_id uuid,p_capability text
) returns boolean
language sql stable security definer
set search_path=pg_catalog,app_private,public
as $$
  select p_seller_organization_id is not null and p_course_id is not null
    and app_private.context_text('capability')=p_capability
    and app_private.context_uuid('seller_organization_id')=p_seller_organization_id
    and exists(
      select 1 from public.seller_organization organization
      join public.seller_membership membership
        on membership.seller_organization_id=organization.id
      where organization.id=p_seller_organization_id
        and organization.status='active'
        and membership.identity_user_id=app_private.context_uuid('identity_user_id')
        and membership.role in ('seller_owner','seller_admin','seller_editor')
        and membership.status='active'
    )
    and exists(
      select 1 from public.course candidate
      where candidate.seller_organization_id=p_seller_organization_id
        and candidate.id=p_course_id and candidate.status<>'archived'
    )
$$
"""

_LEGACY_LOCK_CURRICULUM_ACCESS = """
create or replace function app_private.lock_curriculum_access(
  p_identity_user_id uuid,p_seller_organization_id uuid,
  p_catalog_product_id uuid,p_course_id uuid,p_capability text
) returns void
language plpgsql security definer
set search_path=pg_catalog,app_private,public
as $$
begin
  if app_private.context_text('capability')<>p_capability
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is distinct from
        p_seller_organization_id then
    raise exception 'curriculum context is not authorized' using errcode='28000';
  end if;
  perform 1 from public.seller_organization organization
  join public.seller_membership membership
    on membership.seller_organization_id=organization.id
  where organization.id=p_seller_organization_id
    and organization.status='active'
    and membership.identity_user_id=p_identity_user_id
    and membership.role in ('seller_owner','seller_admin','seller_editor')
    and membership.status='active'
  for share of organization,membership;
  if not found then
    raise exception 'curriculum actor is not authorized' using errcode='28000';
  end if;
  perform 1 from public.product_course linked
  join public.course candidate
    on candidate.seller_organization_id=linked.seller_organization_id
   and candidate.id=linked.course_id
  where linked.seller_organization_id=p_seller_organization_id
    and linked.catalog_product_id=p_catalog_product_id
    and linked.course_id=p_course_id and candidate.status<>'archived'
  for share of linked;
  if not found then
    raise exception 'curriculum course is unavailable' using errcode='28000';
  end if;
end
$$
"""

_LEGACY_PENDING_ASSET_ACCESS = """
create or replace function app_private.product_draft_has_pending_asset_upload(
  p_actor uuid,p_seller uuid,p_product uuid,p_capability text
) returns boolean
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp
as $$
begin
  if p_actor is null or p_seller is null or p_product is null
     or p_capability is null
     or p_capability not in ('catalog.draft.read','catalog.draft.preview')
     or app_private.context_text('capability') is distinct from p_capability
     or app_private.context_uuid('identity_user_id') is distinct from p_actor
     or app_private.context_uuid('seller_organization_id') is distinct from p_seller then
    raise exception 'product draft pending upload context is not authorized'
      using errcode='42501';
  end if;
  perform 1 from public.seller_organization organization
  join public.seller_membership membership
    on membership.seller_organization_id=organization.id
  where organization.id=p_seller and organization.status='active'
    and membership.identity_user_id=p_actor
    and membership.role in ('seller_owner','seller_admin','seller_editor')
    and membership.status='active';
  if not found then
    raise exception 'product draft pending upload actor is not authorized'
      using errcode='28000';
  end if;
  perform 1 from public.catalog_product product
  where product.seller_organization_id=p_seller and product.id=p_product;
  if not found then
    raise exception 'product draft pending upload product is unavailable'
      using errcode='28000';
  end if;
  return exists(
    select 1 from app_private.asset_upload pending_upload
    where pending_upload.seller_organization_id=p_seller
      and pending_upload.catalog_product_id=p_product
      and (
        pending_upload.status in ('uploaded','verifying')
        or (pending_upload.status='pending' and pending_upload.expires_at>clock_timestamp())
      )
  );
end
$$
"""


def _rewrite(signature: str, old: str, new: str, label: str) -> str:
    return f"""
    do $migration$
    declare definition text; changed text;
    begin
      definition:=pg_get_functiondef('{signature}'::regprocedure);
      changed:=replace(definition,$old${old}$old$,$new${new}$new$);
      if changed=definition then
        raise exception 'cannot rewrite {label} authorization' using errcode='55000';
      end if;
      execute changed;
    end $migration$
    """


_OWNER_MUTATIONS = (
    (
        "app_private.create_product_course_draft(uuid,uuid,text,text,text,text,uuid,text,uuid)",
        "            and membership.identity_user_id = p_identity_user_id\n"
        "            and membership.role = 'seller_owner'\n"
        "            and membership.status = 'active'",
        "            and (\n"
        "              (membership.identity_user_id = p_identity_user_id\n"
        "               and membership.role = 'seller_owner'\n"
        "               and membership.status = 'active')\n"
        "              or app_private.platform_superadmin_catalog_allowed(\n"
        "                p_identity_user_id,p_seller_organization_id,'catalog.draft.create'\n"
        "              )\n"
        "            )",
        "product draft create",
    ),
    (
        "app_private.update_product_course_draft(uuid,uuid,uuid,text,uuid,text,text,text,bigint,bigint,uuid,text,uuid)",
        "    and membership.identity_user_id = p_identity_user_id\n"
        "    and membership.role = 'seller_owner'\n"
        "    and membership.status = 'active'",
        "    and (\n"
        "      (membership.identity_user_id = p_identity_user_id\n"
        "       and membership.role = 'seller_owner'\n"
        "       and membership.status = 'active')\n"
        "      or app_private.platform_superadmin_catalog_allowed(\n"
        "        p_identity_user_id,p_seller_organization_id,'catalog.draft.update'\n"
        "      )\n"
        "    )",
        "product metadata update",
    ),
    (
        "app_private.update_product_course_offer_draft(uuid,uuid,uuid,text,uuid,uuid,text,text,text,text,text,bigint,text,text,bigint,bigint,bigint,uuid,text,uuid)",
        "            and membership.identity_user_id = p_identity_user_id\n"
        "            and membership.role = 'seller_owner'\n"
        "            and membership.status = 'active'",
        "            and (\n"
        "              (membership.identity_user_id = p_identity_user_id\n"
        "               and membership.role = 'seller_owner'\n"
        "               and membership.status = 'active')\n"
        "              or app_private.platform_superadmin_catalog_allowed(\n"
        "                p_identity_user_id,p_seller_organization_id,'catalog.draft.update'\n"
        "              )\n"
        "            )",
        "product offer update",
    ),
    (
        "app_private.transition_offer_status(uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid)",
        "    and membership.identity_user_id = p_identity_user_id\n"
        "    and membership.role = 'seller_owner'\n"
        "    and membership.status = 'active'",
        "    and (\n"
        "      (membership.identity_user_id = p_identity_user_id\n"
        "       and membership.role = 'seller_owner'\n"
        "       and membership.status = 'active')\n"
        "      or app_private.platform_superadmin_catalog_allowed(\n"
        "        p_identity_user_id,p_seller_organization_id,'catalog.offer.status.update'\n"
        "      )\n"
        "    )",
        "offer status update",
    ),
    (
        "app_private.publish_product(uuid,uuid,uuid,uuid,bigint,bigint,jsonb,uuid,text,uuid)",
        "            and membership.identity_user_id=p_identity_user_id\n"
        "            and membership.role='seller_owner' and membership.status='active'",
        "            and ((membership.identity_user_id=p_identity_user_id\n"
        "                  and membership.role='seller_owner' and membership.status='active')\n"
        "              or app_private.platform_superadmin_catalog_allowed(\n"
        "                p_identity_user_id,p_seller_organization_id,'catalog.publication.create'\n"
        "              ))",
        "product publication",
    ),
)


def upgrade() -> None:
    statements = [
        _PLATFORM_ALLOWED,
        f"revoke all on function {_PLATFORM_ALLOWED_SIGNATURE} from public",
        f"grant execute on function {_PLATFORM_ALLOWED_SIGNATURE} to appsell_runtime",
        _LIST_ACCESSIBLE,
        f"revoke all on function {_LIST_SIGNATURE} from public",
        f"grant execute on function {_LIST_SIGNATURE} to appsell_runtime",
        _CATALOG_DRAFT_READ_ALLOWED,
        _CATALOG_OWNER_ACTION_ALLOWED,
        _CURRICULUM_ACTION_ALLOWED,
        _LOCK_CURRICULUM_ACCESS,
        _PENDING_ASSET_ACCESS,
        """
        create policy seller_org_catalog_projection_select
        on public.seller_organization for select to appsell_runtime
        using(
          id=app_private.context_uuid('seller_organization_id')
          and app_private.catalog_draft_read_allowed(id)
        )
        """,
    ]
    statements.extend(
        _rewrite(signature, old, new, label)
        for signature, old, new, label in _OWNER_MUTATIONS
    )
    statements.extend(
        [
            f"""
            do $migration$
            declare definition text; changed text;
            begin
              definition:=pg_get_functiondef(
                'app_private.schema_ready_base_0016()'::regprocedure
              );
              changed:=replace(
                definition,$old${_READY_TAIL}$old$,$new${_READY_TAIL_WITH_0032}$new$
              );
              if changed=definition then
                raise exception 'cannot extend 0016 readiness for 0032'
                  using errcode='55000';
              end if;
              execute changed;

              definition:=pg_get_functiondef(
                'app_private.schema_ready_base_0017()'::regprocedure
              );
              changed:=replace(
                definition,$old${_READY_TAIL}$old$,$new${_READY_TAIL_WITH_0032}$new$
              );
              if changed=definition then
                raise exception 'cannot extend 0017 readiness for 0032'
                  using errcode='55000';
              end if;
              execute changed;

              definition:=pg_get_functiondef('app_private.schema_ready()'::regprocedure);
              changed:=replace(
                definition,$old${_READY_ANCHOR}$old$,$new${_READY_ADDITION}$new$
              );
              if changed=definition then
                raise exception 'cannot extend schema readiness for 0032'
                  using errcode='55000';
              end if;
              execute changed;
            end $migration$
            """
        ]
    )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition:=pg_get_functiondef('app_private.schema_ready()'::regprocedure);
          changed:=replace(
            definition,$old${_READY_ADDITION}$old$,$new${_READY_ANCHOR}$new$
          );
          if changed=definition then
            raise exception 'cannot restore schema readiness from 0032'
              using errcode='55000';
          end if;
          execute changed;

          definition:=pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed:=replace(
            definition,$old${_READY_TAIL_WITH_0032}$old$,$new${_READY_TAIL}$new$
          );
          if changed=definition then
            raise exception 'cannot restore 0017 readiness from 0032'
              using errcode='55000';
          end if;
          execute changed;

          definition:=pg_get_functiondef(
            'app_private.schema_ready_base_0016()'::regprocedure
          );
          changed:=replace(
            definition,$old${_READY_TAIL_WITH_0032}$old$,$new${_READY_TAIL}$new$
          );
          if changed=definition then
            raise exception 'cannot restore 0016 readiness from 0032'
              using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """
    ]
    statements.extend(
        _rewrite(signature, new, old, f"restore {label}")
        for signature, old, new, label in reversed(_OWNER_MUTATIONS)
    )
    statements.extend(
        [
            "drop policy seller_org_catalog_projection_select on public.seller_organization",
            _LEGACY_CATALOG_DRAFT_READ_ALLOWED,
            _LEGACY_CATALOG_OWNER_ACTION_ALLOWED,
            _LEGACY_CURRICULUM_ACTION_ALLOWED,
            _LEGACY_LOCK_CURRICULUM_ACCESS,
            _LEGACY_PENDING_ASSET_ACCESS,
            "drop function app_private.list_accessible_sellers(uuid)",
            "drop function app_private.platform_superadmin_catalog_allowed(uuid,uuid,text)",
        ]
    )
    for statement in statements:
        op.execute(statement)
