"""Create identity, platform grants, seller tenancy and append-only audit.

Revision ID: 0001_domain_foundation
Revises:
Create Date: 2026-07-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_domain_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = [
        "create extension if not exists pgcrypto",
        "revoke create on schema public from public",
        "create schema if not exists app_private authorization appsell_owner",
        "revoke all on schema app_private from public",
        """
        create table app_private.context_signing_key (
          singleton boolean primary key default true check (singleton),
          secret bytea not null check (octet_length(secret) = 32),
          created_at timestamptz not null default clock_timestamp()
        )
        """,
        "revoke all on table app_private.context_signing_key from public, appsell_runtime",
        """
        create table app_private.bootstrap_trust_policy (
          singleton boolean primary key default true check (singleton),
          issuer text not null,
          audience text not null,
          client_id text not null,
          attestation_secret bytea not null check (octet_length(attestation_secret) = 32),
          enabled boolean not null default false,
          created_at timestamptz not null default clock_timestamp()
        )
        """,
        "revoke all on table app_private.bootstrap_trust_policy from public, appsell_runtime",
        """
        create function app_private.apply_request_context(
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
        """,
        """
        create function app_private.verified_context()
        returns text[] language plpgsql stable security definer
        set search_path = pg_catalog, app_private, public
        as $$
        declare
          signing_secret bytea;
          payload text := nullif(current_setting('appsell.context_payload', true), '');
          signature text := nullif(current_setting('appsell.context_signature', true), '');
          expected text;
          parts text[];
          now_epoch bigint := extract(epoch from clock_timestamp())::bigint;
        begin
          if payload is null or signature is null or length(signature) <> 64 then
            return null;
          end if;
          select secret into signing_secret
            from app_private.context_signing_key where singleton;
          if signing_secret is null then
            return null;
          end if;
          expected := encode(public.hmac(convert_to(payload, 'UTF8'), signing_secret, 'sha256'), 'hex');
          if expected <> lower(signature) then
            return null;
          end if;
          parts := string_to_array(payload, ':');
          if cardinality(parts) <> 5
             or parts[1] = '' or parts[3] !~ '^[a-z][a-z0-9_.-]{2,63}$'
             or parts[4] !~ '^[0-9]+$' or parts[5] !~ '^[0-9]+$'
             or parts[4]::bigint < now_epoch - 60
             or parts[4]::bigint > now_epoch + 15
             or parts[5]::bigint < parts[4]::bigint
             or parts[5]::bigint < now_epoch
             or parts[5]::bigint > parts[4]::bigint + 120 then
            return null;
          end if;
          return parts;
        end
        $$
        """,
        """
        create function app_private.context_uuid(name text)
        returns uuid language plpgsql stable security definer
        set search_path = pg_catalog, app_private
        as $$
        declare
          context_parts text[] := app_private.verified_context();
          value text;
        begin
          value := case name
            when 'identity_user_id' then context_parts[1]
            when 'seller_organization_id' then context_parts[2]
            else null
          end;
          return nullif(value, '')::uuid;
        exception when invalid_text_representation then
          return null;
        end
        $$
        """,
        """
        create function app_private.context_text(name text)
        returns text language plpgsql stable security definer
        set search_path = pg_catalog, app_private
        as $$
        declare
          context_parts text[] := app_private.verified_context();
        begin
          return case name when 'capability' then context_parts[3] else null end;
        end
        $$
        """,
        "revoke all on function app_private.verified_context() from public",
        "revoke all on function app_private.context_uuid(text) from public",
        "revoke all on function app_private.context_text(text) from public",
        "revoke all on function app_private.apply_request_context(uuid, uuid, text, bigint, bigint, text) from public",
        "grant usage on schema app_private to appsell_runtime",
        "grant execute on function app_private.context_uuid(text) to appsell_runtime",
        "grant execute on function app_private.context_text(text) to appsell_runtime",
        "grant execute on function app_private.apply_request_context(uuid, uuid, text, bigint, bigint, text) to appsell_runtime",
        """
        create table identity_user (
          id uuid primary key default gen_random_uuid(),
          identity_sub text not null unique check (length(identity_sub) between 3 and 255),
          email_normalized text not null check (
            email_normalized = lower(btrim(email_normalized)) and position('@' in email_normalized) > 1
          ),
          email_verified boolean not null default false,
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          unique (id, identity_sub)
        )
        """,
        """
        create table platform_grant (
          id uuid primary key default gen_random_uuid(),
          identity_user_id uuid not null references identity_user(id),
          role text not null check (role in ('platform_superadmin','platform_support','platform_auditor')),
          status text not null check (status in ('active','revoked')),
          granted_at timestamptz not null default clock_timestamp(),
          revoked_at timestamptz,
          granted_by_identity_user_id uuid references identity_user(id),
          unique (identity_user_id, role),
          check ((status = 'revoked') = (revoked_at is not null))
        )
        """,
        "create index platform_grant_status_idx on platform_grant (role, status, identity_user_id)",
        """
        create table superadmin_bootstrap (
          singleton_key text primary key default 'default' check (singleton_key = 'default'),
          identity_user_id uuid not null unique references identity_user(id),
          identity_sub text not null unique,
          assurance text not null check (assurance in ('mfa','passkey')),
          consumed_at timestamptz not null default clock_timestamp(),
          request_id uuid not null unique,
          source_ip_hash text check (source_ip_hash is null or length(source_ip_hash) = 64),
          foreign key (identity_user_id, identity_sub)
            references identity_user (id, identity_sub)
        )
        """,
        """
        create table seller_organization (
          id uuid primary key default gen_random_uuid(),
          slug text not null unique check (slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'),
          display_name text not null check (length(btrim(display_name)) between 2 and 120),
          status text not null check (status in ('active','suspended','closed')),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          unique (id, status)
        )
        """,
        """
        create table seller_membership (
          seller_organization_id uuid not null references seller_organization(id),
          identity_user_id uuid not null references identity_user(id),
          role text not null check (role in ('seller_owner','seller_admin','seller_editor','seller_support')),
          status text not null check (status in ('active','invited','revoked')),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id, identity_user_id)
        )
        """,
        """
        create unique index seller_membership_one_active_owner_idx
          on seller_membership (seller_organization_id)
          where role = 'seller_owner' and status = 'active'
        """,
        "create index seller_membership_identity_idx on seller_membership (identity_user_id, status, seller_organization_id)",
        """
        create table audit_event (
          id uuid primary key default gen_random_uuid(),
          seller_organization_id uuid references seller_organization(id),
          actor_identity_user_id uuid references identity_user(id),
          action text not null check (length(action) between 3 and 120),
          target_type text not null check (length(target_type) between 2 and 80),
          target_id uuid,
          result text not null check (result in ('allowed','denied','succeeded','failed')),
          request_id uuid not null,
          metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
          created_at timestamptz not null default clock_timestamp()
        )
        """,
        "create index audit_event_seller_time_idx on audit_event (seller_organization_id, created_at desc)",
        "create index audit_event_actor_time_idx on audit_event (actor_identity_user_id, created_at desc)",
        "create index audit_event_target_idx on audit_event (target_type, target_id, created_at desc)",
        """
        create function app_private.reject_audit_mutation()
        returns trigger language plpgsql
        set search_path = pg_catalog
        as $$ begin raise exception 'audit events are append-only' using errcode = '55000'; end $$
        """,
        """
        create trigger audit_event_immutable
          before update or delete on audit_event
          for each row execute function app_private.reject_audit_mutation()
        """,
        "revoke all on function app_private.reject_audit_mutation() from public",
        """
        create function app_private.schema_ready()
        returns boolean language sql stable security definer
        set search_path = pg_catalog, app_private
        as $$
          select to_regclass('public.identity_user') is not null
            and to_regclass('public.platform_grant') is not null
            and to_regclass('public.superadmin_bootstrap') is not null
            and to_regclass('public.seller_organization') is not null
            and to_regclass('public.seller_membership') is not null
            and to_regclass('public.audit_event') is not null
            and exists (
              select 1 from public.alembic_version
              where version_num = '0001_domain_foundation'
            )
            and exists (select 1 from app_private.context_signing_key where singleton)
            and exists (select 1 from app_private.bootstrap_trust_policy where singleton)
        $$
        """,
        "revoke all on function app_private.schema_ready() from public",
        "grant execute on function app_private.schema_ready() to appsell_runtime",
        "alter table identity_user enable row level security",
        "alter table identity_user force row level security",
        "alter table platform_grant enable row level security",
        "alter table platform_grant force row level security",
        "alter table seller_organization enable row level security",
        "alter table seller_organization force row level security",
        "alter table seller_membership enable row level security",
        "alter table seller_membership force row level security",
        "alter table audit_event enable row level security",
        "alter table audit_event force row level security",
        "create policy identity_user_owner_all on identity_user to appsell_owner using (true) with check (true)",
        "create policy platform_grant_owner_all on platform_grant to appsell_owner using (true) with check (true)",
        "create policy seller_organization_owner_all on seller_organization to appsell_owner using (true) with check (true)",
        "create policy seller_membership_owner_all on seller_membership to appsell_owner using (true) with check (true)",
        "create policy audit_event_owner_all on audit_event to appsell_owner using (true) with check (true)",
        """
        create policy identity_user_self_select on identity_user for select to appsell_runtime
          using (id = app_private.context_uuid('identity_user_id'))
        """,
        """
        create policy platform_grant_self_select on platform_grant for select to appsell_runtime
          using (identity_user_id = app_private.context_uuid('identity_user_id'))
        """,
        """
        create policy membership_self_select on seller_membership for select to appsell_runtime
          using (identity_user_id = app_private.context_uuid('identity_user_id'))
        """,
        """
        create policy seller_org_member_select on seller_organization for select to appsell_runtime
          using (
            id = app_private.context_uuid('seller_organization_id') and
            exists (
              select 1 from seller_membership membership
              where membership.seller_organization_id = seller_organization.id
                and membership.identity_user_id = app_private.context_uuid('identity_user_id')
                and membership.status = 'active'
            )
          )
        """,
        """
        create policy audit_member_insert on audit_event for insert to appsell_runtime
          with check (
            app_private.context_text('capability') = 'audit.write' and
            actor_identity_user_id = app_private.context_uuid('identity_user_id') and
            seller_organization_id = app_private.context_uuid('seller_organization_id') and
            exists (
              select 1 from seller_membership membership
              where membership.seller_organization_id = audit_event.seller_organization_id
                and membership.identity_user_id = app_private.context_uuid('identity_user_id')
                and membership.status = 'active'
            )
          )
        """,
        """
        create policy audit_member_select on audit_event for select to appsell_runtime
          using (
            app_private.context_text('capability') = 'audit.read' and
            seller_organization_id = app_private.context_uuid('seller_organization_id') and
            exists (
              select 1 from seller_membership membership
              where membership.seller_organization_id = audit_event.seller_organization_id
                and membership.identity_user_id = app_private.context_uuid('identity_user_id')
                and membership.status = 'active'
                and membership.role in ('seller_owner','seller_admin')
            )
          )
        """,
        """
        create function app_private.consume_superadmin_bootstrap(
          identity_user_id uuid,
          identity_sub text,
          email_normalized text,
          email_verified boolean,
          assurance text,
          issuer text,
          audience text,
          client_id text,
          request_id uuid,
          source_ip_hash text,
          attestation_issued_at bigint,
          attestation_expires_at bigint,
          attestation_signature text
        ) returns uuid
        language plpgsql security definer
        set search_path = pg_catalog, app_private, public
        as $$
        declare
          signing_secret bytea;
          bootstrap_enabled boolean;
          expected_issuer text;
          expected_audience text;
          expected_client_id text;
          payload text;
          expected_signature text;
          now_epoch bigint := extract(epoch from clock_timestamp())::bigint;
        begin
          if app_private.context_text('capability') <> 'platform.bootstrap'
             or app_private.context_uuid('identity_user_id') is distinct from identity_user_id
             or app_private.context_uuid('seller_organization_id') is not null then
            raise exception 'bootstrap context is not authorized' using errcode = '28000';
          end if;
          if identity_sub is null or length(identity_sub) not between 3 and 255
             or email_verified is not true
             or email_normalized is null
             or lower(btrim(email_normalized)) <> 'comercial@autonomia.site'
             or assurance is null
             or assurance not in ('mfa', 'passkey')
             or request_id is null
             or (source_ip_hash is not null and source_ip_hash !~ '^[0-9a-f]{64}$') then
            raise exception 'bootstrap claims do not satisfy policy' using errcode = '28000';
          end if;
          if attestation_issued_at is null or attestation_expires_at is null
             or attestation_issued_at < now_epoch - 60
             or attestation_issued_at > now_epoch + 15
             or attestation_expires_at < attestation_issued_at
             or attestation_expires_at < now_epoch
             or attestation_expires_at > attestation_issued_at + 120 then
            raise exception 'bootstrap attestation expired' using errcode = '28000';
          end if;
          select policy.issuer, policy.audience, policy.client_id,
                 policy.attestation_secret, policy.enabled
            into strict expected_issuer, expected_audience, expected_client_id,
                        signing_secret, bootstrap_enabled
            from app_private.bootstrap_trust_policy policy
            where singleton for update;
          if bootstrap_enabled is not true then
            raise exception 'superadmin bootstrap is disabled' using errcode = '28000';
          end if;
          if issuer is distinct from expected_issuer
             or audience is distinct from expected_audience
             or client_id is distinct from expected_client_id then
            raise exception 'bootstrap token trust policy mismatch' using errcode = '28000';
          end if;
          payload := 'appsell.bootstrap.v1:' || identity_user_id::text ||
            ':' || encode(public.digest(convert_to(identity_sub, 'UTF8'), 'sha256'), 'hex') ||
            ':' || encode(public.digest(convert_to(lower(btrim(email_normalized)), 'UTF8'), 'sha256'), 'hex') ||
            ':' || email_verified::text || ':' || assurance ||
            ':' || encode(public.digest(convert_to(issuer, 'UTF8'), 'sha256'), 'hex') ||
            ':' || encode(public.digest(convert_to(audience, 'UTF8'), 'sha256'), 'hex') ||
            ':' || encode(public.digest(convert_to(client_id, 'UTF8'), 'sha256'), 'hex') ||
            ':' || request_id::text || ':' || attestation_issued_at::text ||
            ':' || attestation_expires_at::text;
          expected_signature := encode(
            public.hmac(convert_to(payload, 'UTF8'), signing_secret, 'sha256'), 'hex'
          );
          if attestation_signature is null or length(attestation_signature) <> 64
             or expected_signature is distinct from lower(attestation_signature) then
            raise exception 'bootstrap attestation signature invalid' using errcode = '28000';
          end if;

          perform pg_advisory_xact_lock(hashtextextended('appsell.superadmin.bootstrap', 0));
          if exists (select 1 from public.superadmin_bootstrap)
             or exists (
               select 1 from public.platform_grant
               where role = 'platform_superadmin' and status = 'active'
             ) then
            raise exception 'superadmin bootstrap already consumed' using errcode = '23505';
          end if;

          insert into public.identity_user (
            id, identity_sub, email_normalized, email_verified
          ) values (
            identity_user_id, identity_sub, lower(btrim(email_normalized)), true
          ) on conflict (id) do update set
              email_normalized = excluded.email_normalized,
              email_verified = true,
              updated_at = clock_timestamp()
            where public.identity_user.identity_sub = excluded.identity_sub;
          if not found then
            raise exception 'bootstrap identity binding conflicts with existing subject'
              using errcode = '28000';
          end if;
          insert into public.platform_grant (
            identity_user_id, role, status, granted_by_identity_user_id
          ) values (
            identity_user_id, 'platform_superadmin', 'active', identity_user_id
          );
          insert into public.superadmin_bootstrap (
            identity_user_id, identity_sub, assurance, request_id, source_ip_hash
          ) values (
            identity_user_id, identity_sub, assurance, request_id, source_ip_hash
          );
          insert into public.audit_event (
            actor_identity_user_id, action, target_type, target_id,
            result, request_id, metadata
          ) values (
            identity_user_id, 'platform.superadmin_bootstrapped', 'identity_user',
            identity_user_id, 'succeeded', request_id,
            jsonb_build_object('assurance', assurance)
          );
          update app_private.bootstrap_trust_policy set enabled = false where singleton;
          return identity_user_id;
        end
        $$
        """,
        "revoke all on function app_private.consume_superadmin_bootstrap(uuid, text, text, boolean, text, text, text, text, uuid, text, bigint, bigint, text) from public",
        "grant execute on function app_private.consume_superadmin_bootstrap(uuid, text, text, boolean, text, text, text, text, uuid, text, bigint, bigint, text) to appsell_runtime",
        "grant usage on schema public to appsell_runtime",
        "grant select on identity_user, platform_grant, seller_organization, seller_membership, audit_event to appsell_runtime",
        "grant insert on audit_event to appsell_runtime",
        "revoke insert, update, delete on superadmin_bootstrap from appsell_runtime",
        "revoke update, delete on audit_event from appsell_runtime",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("AppSell migrations are forward-only")
