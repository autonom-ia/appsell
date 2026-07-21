"""Add audited platform suspension and reactivation of seller tenants.

Revision ID: 0019_tenant_suspension
Revises: 0018_offer_pricing

This migration is deliberately reversible. It changes no tenant-owned child row:
only ``seller_organization.status`` is updated, preserving every composite key and
foreign-key graph. Tenant-scoped request context fails closed while suspended.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019_tenant_suspension"
down_revision: str | None = "0018_offer_pricing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_APPLY_REQUEST_CONTEXT_0019 = """
create or replace function app_private.apply_request_context(
  identity_user_id uuid,
  seller_organization_id uuid,
  capability text,
  issued_at bigint,
  expires_at bigint,
  signature text
) returns void
language plpgsql security definer
set search_path = pg_catalog, app_private
as $$
declare
  signing_secret bytea;
  payload text;
  expected text;
  now_epoch bigint := extract(epoch from clock_timestamp())::bigint;
begin
  if identity_user_id is null or capability is null
     or capability !~ '^[a-z][a-z0-9_.-]{2,63}$' then
    raise exception 'invalid request context' using errcode = '22023';
  end if;
  if issued_at < now_epoch - 60 or issued_at > now_epoch + 15
     or expires_at < issued_at or expires_at < now_epoch
     or expires_at > issued_at + 120 then
    raise exception 'expired request context' using errcode = '28000';
  end if;
  select secret into strict signing_secret
    from app_private.context_signing_key where singleton;
  payload := identity_user_id::text || ':' || coalesce(seller_organization_id::text, '') ||
             ':' || capability || ':' || issued_at::text || ':' || expires_at::text;
  expected := encode(public.hmac(convert_to(payload, 'UTF8'), signing_secret, 'sha256'), 'hex');
  if signature is null or length(signature) <> 64 or expected <> lower(signature) then
    raise exception 'invalid request context signature' using errcode = '28000';
  end if;
  if seller_organization_id is not null and not exists (
    select 1 from public.seller_organization organization
    where organization.id = seller_organization_id and organization.status = 'active'
  ) then
    raise exception 'tenant context is unavailable' using errcode = '28000';
  end if;
  perform set_config('appsell.context_payload', payload, true);
  perform set_config('appsell.context_signature', lower(signature), true);
end
$$
"""


_APPLY_REQUEST_CONTEXT_0018 = """
create or replace function app_private.apply_request_context(
  identity_user_id uuid,
  seller_organization_id uuid,
  capability text,
  issued_at bigint,
  expires_at bigint,
  signature text
) returns void
language plpgsql security definer
set search_path = pg_catalog, app_private
as $$
declare
  signing_secret bytea;
  payload text;
  expected text;
  now_epoch bigint := extract(epoch from clock_timestamp())::bigint;
begin
  if identity_user_id is null or capability is null or capability !~ '^[a-z][a-z0-9_.-]{2,63}$' then
    raise exception 'invalid request context' using errcode = '22023';
  end if;
  if issued_at < now_epoch - 60 or issued_at > now_epoch + 15
     or expires_at < issued_at or expires_at < now_epoch
     or expires_at > issued_at + 120 then
    raise exception 'expired request context' using errcode = '28000';
  end if;
  select secret into strict signing_secret
    from app_private.context_signing_key where singleton;
  payload := identity_user_id::text || ':' || coalesce(seller_organization_id::text, '') ||
             ':' || capability || ':' || issued_at::text || ':' || expires_at::text;
  expected := encode(public.hmac(convert_to(payload, 'UTF8'), signing_secret, 'sha256'), 'hex');
  if signature is null or length(signature) <> 64 or expected <> lower(signature) then
    raise exception 'invalid request context signature' using errcode = '28000';
  end if;
  perform set_config('appsell.context_payload', payload, true);
  perform set_config('appsell.context_signature', lower(signature), true);
end
$$
"""


def upgrade() -> None:
    statements = [
        _APPLY_REQUEST_CONTEXT_0019,
        "revoke all on function app_private.apply_request_context(uuid,uuid,text,bigint,bigint,text) from public",
        "grant execute on function app_private.apply_request_context(uuid,uuid,text,bigint,bigint,text) to appsell_runtime",
        "drop policy seller_org_member_select on public.seller_organization",
        """
        create policy seller_org_member_select on public.seller_organization
          for select to appsell_runtime
          using (
            seller_organization.status = 'active'
            and exists (
              select 1 from public.seller_membership membership
              where membership.seller_organization_id = seller_organization.id
                and membership.identity_user_id = app_private.context_uuid('identity_user_id')
                and membership.status = 'active'
            ) and (
              (
                app_private.context_text('capability') = 'seller.read'
                and id = app_private.context_uuid('seller_organization_id')
              ) or (
                app_private.context_text('capability') = 'seller.list'
                and app_private.context_uuid('seller_organization_id') is null
              )
            )
          )
        """,
        """
        create function app_private.set_tenant_status(
          p_identity_user_id uuid,
          p_identity_sub text,
          p_seller_organization_id uuid,
          p_target_status text,
          p_reason text,
          p_request_id uuid
        ) returns jsonb
        language plpgsql security definer
        set search_path = pg_catalog, app_private
        as $$
        declare
          expected_capability text;
          audit_action text;
          tenant public.seller_organization%rowtype;
          previous_status text;
          applied boolean := false;
        begin
          expected_capability := case p_target_status
            when 'suspended' then 'platform.tenant.suspend'
            when 'active' then 'platform.tenant.reactivate'
            else null
          end;
          audit_action := case p_target_status
            when 'suspended' then 'platform.tenant.suspended'
            when 'active' then 'platform.tenant.reactivated'
            else null
          end;
          if expected_capability is null
             or app_private.context_text('capability') is distinct from expected_capability
             or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
             or app_private.context_uuid('seller_organization_id') is not null then
            raise exception 'platform tenant status context is not authorized'
              using errcode = '28000';
          end if;
          if p_identity_user_id is null or p_identity_sub is null
             or p_identity_sub <> btrim(p_identity_sub)
             or octet_length(p_identity_sub) not between 3 and 255
             or p_identity_sub ~ '[[:cntrl:]]'
             or p_seller_organization_id is null or p_request_id is null
             or p_reason is null or p_reason <> btrim(p_reason)
             or octet_length(p_reason) not between 8 and 500
             or p_reason ~ '[[:cntrl:]]' then
            raise exception 'invalid platform tenant status request' using errcode = '22023';
          end if;
          if not app_private.lock_active_superadmin(p_identity_user_id, p_identity_sub) then
            raise exception 'active superadmin grant is unavailable' using errcode = '28000';
          end if;

          select * into tenant
          from public.seller_organization organization
          where organization.id = p_seller_organization_id
          for update;
          if not found then
            return null;
          end if;
          if tenant.status = 'closed' then
            raise exception 'closed tenant status is immutable' using errcode = '28000';
          end if;
          previous_status := tenant.status;
          if tenant.status is distinct from p_target_status then
            update public.seller_organization organization
              set status = p_target_status, updated_at = clock_timestamp()
              where organization.id = tenant.id;
            applied := true;
          end if;
          insert into public.audit_event (
            seller_organization_id, actor_identity_user_id, action, target_type,
            target_id, result, request_id, metadata
          ) values (
            tenant.id, p_identity_user_id, audit_action, 'seller_organization',
            tenant.id, 'succeeded', p_request_id,
            jsonb_build_object(
              'reason', p_reason,
              'previous_status', previous_status,
              'new_status', p_target_status,
              'applied', applied
            )
          );
          return jsonb_build_object(
            'id', tenant.id,
            'status', p_target_status,
            'applied', applied
          );
        end
        $$
        """,
        "revoke all on function app_private.set_tenant_status(uuid,text,uuid,text,text,uuid) from public",
        "grant execute on function app_private.set_tenant_status(uuid,text,uuid,text,text,uuid) to appsell_runtime",
        r"""
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0016()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing')$needle$,
            $replacement$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_tenant_suspension')$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0016 schema readiness revision' using errcode = '55000';
          end if;
          execute changed;

          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$version_num in ('0017_pdf_preraster','0018_offer_pricing')$needle$,
            $replacement$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_tenant_suspension')$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0017 schema readiness revision' using errcode = '55000';
          end if;
          execute changed;

          alter function app_private.schema_ready() rename to schema_ready_base_0018;
        end $migration$
        """,
        "revoke all on function app_private.schema_ready_base_0018() from public,appsell_runtime",
        """
        create function app_private.schema_ready() returns boolean
        language sql stable security definer set search_path = pg_catalog, app_private as $$
          select app_private.schema_ready_base_0018()
            and to_regprocedure(
              'app_private.set_tenant_status(uuid,text,uuid,text,text,uuid)'
            ) is not null
            and exists (
              select 1 from pg_catalog.pg_class relation
              join pg_catalog.pg_namespace namespace on namespace.oid = relation.relnamespace
              where namespace.nspname = 'public'
                and relation.relname = 'seller_organization'
                and relation.relrowsecurity and relation.relforcerowsecurity
            )
            and exists (
              select 1 from public.alembic_version
              where version_num = '0019_tenant_suspension'
            )
        $$
        """,
        "revoke all on function app_private.schema_ready() from public",
        "grant execute on function app_private.schema_ready() to appsell_runtime",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        "drop function app_private.schema_ready()",
        "alter function app_private.schema_ready_base_0018() rename to schema_ready",
        "revoke all on function app_private.schema_ready() from public",
        "grant execute on function app_private.schema_ready() to appsell_runtime",
        r"""
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0016()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_tenant_suspension')$needle$,
            $replacement$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing')$replacement$
          );
          if changed = definition then
            raise exception 'cannot restore 0016 schema readiness revision' using errcode = '55000';
          end if;
          execute changed;

          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_tenant_suspension')$needle$,
            $replacement$version_num in ('0017_pdf_preraster','0018_offer_pricing')$replacement$
          );
          if changed = definition then
            raise exception 'cannot restore 0017 schema readiness revision' using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        "drop function app_private.set_tenant_status(uuid,text,uuid,text,text,uuid)",
        "drop policy seller_org_member_select on public.seller_organization",
        """
        create policy seller_org_member_select on public.seller_organization
          for select to appsell_runtime
          using (
            exists (
              select 1 from public.seller_membership membership
              where membership.seller_organization_id = seller_organization.id
                and membership.identity_user_id = app_private.context_uuid('identity_user_id')
                and membership.status = 'active'
            ) and (
              (
                app_private.context_text('capability') = 'seller.read'
                and id = app_private.context_uuid('seller_organization_id')
              ) or (
                app_private.context_text('capability') = 'seller.list'
                and app_private.context_uuid('seller_organization_id') is null
              )
            )
          )
        """,
        _APPLY_REQUEST_CONTEXT_0018,
        "revoke all on function app_private.apply_request_context(uuid,uuid,text,bigint,bigint,text) from public",
        "grant execute on function app_private.apply_request_context(uuid,uuid,text,bigint,bigint,text) to appsell_runtime",
    ]
    for statement in statements:
        op.execute(statement)
