"""BFF replay protection, issuer-scoped identity sync, and seller onboarding.

Revision ID: 0002_bff_api_onboarding
Revises: 0001_domain_foundation
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_bff_api_onboarding"
down_revision: str | None = "0001_domain_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = [
        """
        do $$ begin
          if exists (select 1 from public.identity_user) then
            raise exception '0002 requires an empty Local/CI identity foundation; migrate identity issuer bindings explicitly before upgrade';
          end if;
        end $$
        """,
        "alter table identity_user add column identity_issuer text",
        "alter table identity_user alter column identity_issuer set not null",
        "alter table identity_user add constraint identity_user_issuer_format check (length(identity_issuer) between 3 and 512 and identity_issuer = btrim(identity_issuer) and identity_issuer !~ '[[:cntrl:]]')",
        "alter table identity_user drop constraint identity_user_identity_sub_key",
        "alter table identity_user add constraint identity_user_issuer_sub_key unique (identity_issuer, identity_sub)",
        "alter table seller_organization add constraint seller_organization_slug_length check (length(slug) between 3 and 63)",
        "alter table seller_organization add constraint seller_organization_display_name_strict check (display_name = btrim(display_name) and display_name !~ '[[:cntrl:]]')",
        """
        create function app_private.identity_user_id(
          p_identity_issuer text,
          p_identity_sub text
        ) returns uuid
        language plpgsql immutable security definer
        set search_path = pg_catalog, public
        as $$
        declare
          namespace_bytes bytea := uuid_send('6ba7b811-9dad-11d1-80b4-00c04fd430c8'::uuid);
          identifier bytea;
          encoded text;
        begin
          if p_identity_issuer is null or p_identity_sub is null then return null; end if;
          identifier := substring(
            public.digest(
              namespace_bytes || convert_to(p_identity_issuer, 'UTF8') ||
              decode('00', 'hex') || convert_to(p_identity_sub, 'UTF8'),
              'sha1'
            ) from 1 for 16
          );
          identifier := set_byte(identifier, 6, (get_byte(identifier, 6) & 15) | 80);
          identifier := set_byte(identifier, 8, (get_byte(identifier, 8) & 63) | 128);
          encoded := encode(identifier, 'hex');
          return (
            substring(encoded, 1, 8) || '-' || substring(encoded, 9, 4) || '-' ||
            substring(encoded, 13, 4) || '-' || substring(encoded, 17, 4) || '-' ||
            substring(encoded, 21, 12)
          )::uuid;
        end
        $$
        """,
        "revoke all on function app_private.identity_user_id(text, text) from public",
        """
        create table app_private.bff_request_replay (
          request_id uuid primary key,
          expires_at timestamptz not null,
          consumed_at timestamptz not null default clock_timestamp(),
          check (expires_at > consumed_at - interval '15 seconds')
        )
        """,
        "create index bff_request_replay_expiry_idx on app_private.bff_request_replay (expires_at)",
        "revoke all on table app_private.bff_request_replay from public, appsell_runtime",
        """
        create table app_private.seller_onboarding_request (
          idempotency_key uuid primary key,
          identity_user_id uuid not null references public.identity_user(id),
          body_sha256 text not null check (body_sha256 ~ '^[0-9a-f]{64}$'),
          seller_organization_id uuid not null unique references public.seller_organization(id),
          created_at timestamptz not null default clock_timestamp()
        )
        """,
        "create index seller_onboarding_request_actor_idx on app_private.seller_onboarding_request (identity_user_id, created_at desc)",
        "revoke all on table app_private.seller_onboarding_request from public, appsell_runtime",
        """
        create function app_private.consume_bff_request(
          request_id uuid,
          expires_at_epoch bigint
        ) returns void
        language plpgsql security definer
        set search_path = pg_catalog, app_private
        as $$
        declare
          now_epoch bigint := extract(epoch from clock_timestamp())::bigint;
        begin
          if request_id is null or expires_at_epoch is null
             or expires_at_epoch < now_epoch
             or expires_at_epoch > now_epoch + 120 then
            raise exception 'invalid or expired BFF request' using errcode = '28000';
          end if;
          with expired as (
            select replay.request_id
            from app_private.bff_request_replay replay
            where replay.expires_at < clock_timestamp() - interval '5 minutes'
            order by replay.expires_at
            for update skip locked
            limit 100
          )
          delete from app_private.bff_request_replay replay
          using expired where replay.request_id = expired.request_id;
          insert into app_private.bff_request_replay (request_id, expires_at)
          values (request_id, to_timestamp(expires_at_epoch));
        end
        $$
        """,
        "revoke all on function app_private.consume_bff_request(uuid, bigint) from public",
        "grant execute on function app_private.consume_bff_request(uuid, bigint) to appsell_runtime",
        """
        create function app_private.sync_identity(
          p_identity_user_id uuid,
          p_identity_issuer text,
          p_identity_sub text,
          p_email_normalized text,
          p_email_verified boolean
        ) returns uuid
        language plpgsql security definer
        set search_path = pg_catalog, app_private, public
        as $$
        declare
          resolved_identity_id uuid;
          normalized_email text := lower(btrim(p_email_normalized));
        begin
          if app_private.context_text('capability') <> 'identity.sync'
             or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
             or app_private.context_uuid('seller_organization_id') is not null then
            raise exception 'identity sync context is not authorized' using errcode = '28000';
          end if;
          if p_identity_user_id is null
             or p_identity_user_id is distinct from app_private.identity_user_id(
               p_identity_issuer, p_identity_sub
             )
             or p_identity_issuer is null or p_identity_issuer <> btrim(p_identity_issuer)
             or length(convert_to(p_identity_issuer, 'UTF8')) not between 3 and 255
             or p_identity_issuer ~ '[[:cntrl:]]'
             or p_identity_sub is null or p_identity_sub <> btrim(p_identity_sub)
             or length(convert_to(p_identity_sub, 'UTF8')) not between 3 and 255
             or p_identity_sub ~ '[[:cntrl:]]'
             or p_email_normalized is null or p_email_normalized <> normalized_email
             or length(normalized_email) not between 3 and 320
             or position('@' in normalized_email) <= 1
             or p_email_verified is null then
            raise exception 'invalid identity claims' using errcode = '22023';
          end if;

          insert into public.identity_user (
            id, identity_issuer, identity_sub, email_normalized, email_verified
          ) values (
            p_identity_user_id, p_identity_issuer, p_identity_sub,
            normalized_email, p_email_verified
          )
          on conflict on constraint identity_user_issuer_sub_key do update set
            email_normalized = excluded.email_normalized,
            email_verified = excluded.email_verified,
            updated_at = clock_timestamp()
          where public.identity_user.id = excluded.id
          returning id into resolved_identity_id;
          if resolved_identity_id is null then
            raise exception 'identity binding conflicts with existing user' using errcode = '28000';
          end if;
          return resolved_identity_id;
        end
        $$
        """,
        "revoke all on function app_private.sync_identity(uuid, text, text, text, boolean) from public",
        "grant execute on function app_private.sync_identity(uuid, text, text, text, boolean) to appsell_runtime",
        """
        create function app_private.onboard_seller(
          p_identity_user_id uuid,
          p_slug text,
          p_display_name text,
          p_idempotency_key uuid,
          p_body_sha256 text,
          p_request_id uuid
        ) returns uuid
        language plpgsql security definer
        set search_path = pg_catalog, app_private, public
        as $$
        declare
          existing_request app_private.seller_onboarding_request%rowtype;
          seller_id uuid;
        begin
          if app_private.context_text('capability') <> 'seller.onboard'
             or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
             or app_private.context_uuid('seller_organization_id') is not null then
            raise exception 'seller onboarding context is not authorized' using errcode = '28000';
          end if;
          if p_identity_user_id is null or p_idempotency_key is null or p_request_id is null
             or p_slug is null or p_slug !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'
             or length(p_slug) not between 3 and 63
             or p_display_name is null or p_display_name <> btrim(p_display_name)
             or length(p_display_name) not between 2 and 120
             or p_display_name ~ '[[:cntrl:]]'
             or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$' then
            raise exception 'invalid seller onboarding request' using errcode = '22023';
          end if;

          perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text, 0));
          select * into existing_request
            from app_private.seller_onboarding_request request
            where request.idempotency_key = p_idempotency_key;
          if found then
            if existing_request.identity_user_id = p_identity_user_id
               and existing_request.body_sha256 = p_body_sha256 then
              return existing_request.seller_organization_id;
            end if;
            raise exception 'seller onboarding idempotency conflict' using errcode = '23505';
          end if;

          perform pg_advisory_xact_lock(
            hashtextextended('appsell.seller.owner:' || p_identity_user_id::text, 0)
          );
          if not exists (select 1 from public.identity_user where id = p_identity_user_id) then
            raise exception 'identity is unavailable' using errcode = '23503';
          end if;
          if exists (
            select 1 from public.seller_membership membership
            where membership.identity_user_id = p_identity_user_id
              and membership.role = 'seller_owner'
              and membership.status = 'active'
          ) then
            raise exception 'identity already owns a seller' using errcode = '23505';
          end if;

          insert into public.seller_organization (slug, display_name, status)
          values (p_slug, p_display_name, 'active') returning id into seller_id;
          insert into public.seller_membership (
            seller_organization_id, identity_user_id, role, status
          ) values (
            seller_id, p_identity_user_id, 'seller_owner', 'active'
          );
          insert into app_private.seller_onboarding_request (
            idempotency_key, identity_user_id, body_sha256, seller_organization_id
          ) values (
            p_idempotency_key, p_identity_user_id, p_body_sha256, seller_id
          );
          insert into public.audit_event (
            seller_organization_id, actor_identity_user_id, action, target_type,
            target_id, result, request_id, metadata
          ) values (
            seller_id, p_identity_user_id, 'seller.onboarded', 'seller_organization',
            seller_id, 'succeeded', p_request_id,
            jsonb_build_object('slug', p_slug)
          );
          return seller_id;
        end
        $$
        """,
        "revoke all on function app_private.onboard_seller(uuid, text, text, uuid, text, uuid) from public",
        "grant execute on function app_private.onboard_seller(uuid, text, text, uuid, text, uuid) to appsell_runtime",
        "drop policy identity_user_self_select on identity_user",
        """
        create policy identity_user_self_select on identity_user for select to appsell_runtime
          using (
            id = app_private.context_uuid('identity_user_id') and
            app_private.context_text('capability') in (
              'identity.read', 'platform.grants.read', 'seller.list', 'seller.onboard', 'seller.read'
            )
          )
        """,
        "drop policy platform_grant_self_select on platform_grant",
        """
        create policy platform_grant_self_select on platform_grant for select to appsell_runtime
          using (
            identity_user_id = app_private.context_uuid('identity_user_id') and
            app_private.context_text('capability') = 'platform.grants.read'
          )
        """,
        "drop policy membership_self_select on seller_membership",
        """
        create policy membership_self_select on seller_membership for select to appsell_runtime
          using (
            identity_user_id = app_private.context_uuid('identity_user_id') and
            app_private.context_text('capability') in (
              'audit.read', 'audit.write', 'seller.list', 'seller.read'
            )
          )
        """,
        "drop policy seller_org_member_select on seller_organization",
        """
        create policy seller_org_member_select on seller_organization for select to appsell_runtime
          using (
            exists (
              select 1 from seller_membership membership
              where membership.seller_organization_id = seller_organization.id
                and membership.identity_user_id = app_private.context_uuid('identity_user_id')
                and membership.status = 'active'
            ) and (
              (
                app_private.context_text('capability') = 'seller.read' and
                id = app_private.context_uuid('seller_organization_id')
              ) or (
                app_private.context_text('capability') = 'seller.list' and
                app_private.context_uuid('seller_organization_id') is null
              )
            )
          )
        """,
        """
        create or replace function app_private.consume_superadmin_bootstrap(
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
          if identity_sub is null or identity_sub <> btrim(identity_sub)
             or length(convert_to(identity_sub, 'UTF8')) not between 3 and 255
             or identity_sub ~ '[[:cntrl:]]'
             or issuer is null or issuer <> btrim(issuer)
             or length(convert_to(issuer, 'UTF8')) not between 3 and 255
             or issuer ~ '[[:cntrl:]]'
             or identity_user_id is distinct from app_private.identity_user_id(issuer, identity_sub)
             or email_verified is not true
             or email_normalized is null
             or lower(btrim(email_normalized)) <> 'comercial@autonomia.site'
             or assurance is null or assurance not in ('mfa', 'passkey')
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
            id, identity_issuer, identity_sub, email_normalized, email_verified
          ) values (
            identity_user_id, issuer, identity_sub, lower(btrim(email_normalized)), true
          ) on conflict (id) do update set
              identity_issuer = excluded.identity_issuer,
              email_normalized = excluded.email_normalized,
              email_verified = true,
              updated_at = clock_timestamp()
            where public.identity_user.identity_issuer = excluded.identity_issuer
              and public.identity_user.identity_sub = excluded.identity_sub;
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
        """
        create or replace function app_private.schema_ready()
        returns boolean language sql stable security definer
        set search_path = pg_catalog, app_private
        as $$
          select to_regclass('public.identity_user') is not null
            and to_regclass('public.platform_grant') is not null
            and to_regclass('public.superadmin_bootstrap') is not null
            and to_regclass('public.seller_organization') is not null
            and to_regclass('public.seller_membership') is not null
            and to_regclass('public.audit_event') is not null
            and to_regclass('app_private.bff_request_replay') is not null
            and to_regclass('app_private.seller_onboarding_request') is not null
            and exists (
              select 1 from public.alembic_version
              where version_num = '0002_bff_api_onboarding'
            )
            and exists (select 1 from app_private.context_signing_key where singleton)
            and exists (select 1 from app_private.bootstrap_trust_policy where singleton)
        $$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("AppSell migrations are forward-only")
