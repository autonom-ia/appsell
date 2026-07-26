"""Provider-driven refresh of the payout-account status cache.

The producer's bind (0021) is a USER action: capability + tenant context +
membership. The ``account.updated`` webhook is a PROVIDER action: it knows only
the provider account id — there is no user, no session, and no seller in the
caller's hands. Forcing it through the tenant-gated upsert would mean teaching
the BFF to look up the seller first, which turns a blind cache refresh into a
cross-tenant read primitive. Instead the sync function resolves the seller
itself, from the UNIQUE ``(provider, provider_account_id)`` binding, and can
update nothing else.

Gates are the inverse of the upsert's: the context must carry the dedicated
worker capability AND NO TENANT — a user-scoped context (which always carries
its seller) is refused, so the web's payout route can never reach this function
with a browser-driven request, and the webhook's worker context can never reach
the user-facing upsert (whose gate requires a tenant match).

An UNKNOWN account id returns null instead of raising: the provider retries
non-2xx deliveries for days, and an event for an account this platform never
bound (deleted orphan from a lost bind race, another integration on the same
platform account) can never become bindable by retrying.

Status changes are audited as ``payout_account.status_changed`` with a NULL
actor — there is no human behind a provider webhook, and inventing a synthetic
identity row for it would poison the identity table. The metadata carries
``"source": "provider"`` so the trail distinguishes producer-driven refreshes
(0021's upsert, real actor) from provider-driven ones.
"""

from __future__ import annotations

from alembic import op

revision = "0023_payout_status_sync"
down_revision = "0022_payout_repoint_audit"
branch_labels = None
depends_on = None

_SYNC_SIGNATURE = (
    "app_private.sync_payout_account_status(text,text,boolean,boolean,boolean,text,uuid)"
)

_READY_ANCHOR = "select app_private.schema_ready_base_0017()"
_READY_ADDITION = """select app_private.schema_ready_base_0017()
            and to_regprocedure(
              'app_private.sync_payout_account_status(text,text,boolean,boolean,boolean,text,uuid)'
            ) is not null"""


def upgrade() -> None:
    statements = [
        """
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0016()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit')$needle$,
            $replacement$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit','0023_payout_status_sync')$replacement$
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
            $needle$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit')$needle$,
            $replacement$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit','0023_payout_status_sync')$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0017 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        """
        create function app_private.sync_payout_account_status(
          p_provider text, p_provider_account_id text,
          p_charges_enabled boolean, p_payouts_enabled boolean,
          p_details_submitted boolean, p_disabled_reason text,
          p_request_id uuid
        )
        returns uuid language plpgsql volatile security definer
        set search_path=pg_catalog,app_private,public,pg_temp as $$
        declare previous record;
        begin
          if app_private.context_text('capability')
               is distinct from 'commerce.payout.sync'
            or app_private.context_uuid('seller_organization_id') is not null
            or p_request_id is null
          then
            raise exception 'payout status sync denied' using errcode = '42501';
          end if;
          select account.id, account.seller_organization_id,
                 account.charges_enabled, account.payouts_enabled,
                 account.details_submitted
            into previous
            from public.seller_payout_account account
            where account.provider = p_provider
              and account.provider_account_id = p_provider_account_id
            for update;
          if previous.id is null then
            return null;
          end if;
          if (coalesce(p_charges_enabled, previous.charges_enabled),
              coalesce(p_payouts_enabled, previous.payouts_enabled),
              coalesce(p_details_submitted, previous.details_submitted))
             is distinct from
             (previous.charges_enabled, previous.payouts_enabled,
              previous.details_submitted)
          then
            insert into public.audit_event(
              seller_organization_id, actor_identity_user_id, action, target_type,
              target_id, result, request_id, metadata
            ) values (
              previous.seller_organization_id, null,
              'payout_account.status_changed', 'seller_payout_account',
              previous.id, 'succeeded', p_request_id,
              jsonb_build_object(
                'source', 'provider',
                'charges_enabled', jsonb_build_object(
                  'from', previous.charges_enabled,
                  'to', coalesce(p_charges_enabled, previous.charges_enabled)),
                'payouts_enabled', jsonb_build_object(
                  'from', previous.payouts_enabled,
                  'to', coalesce(p_payouts_enabled, previous.payouts_enabled)),
                'details_submitted', jsonb_build_object(
                  'from', previous.details_submitted,
                  'to', coalesce(p_details_submitted, previous.details_submitted))
              )
            );
          end if;
          update public.seller_payout_account
            set charges_enabled = coalesce(p_charges_enabled, charges_enabled),
                payouts_enabled = coalesce(p_payouts_enabled, payouts_enabled),
                details_submitted = coalesce(p_details_submitted, details_submitted),
                disabled_reason = p_disabled_reason,
                status_synced_at = clock_timestamp(),
                updated_at = clock_timestamp()
            where id = previous.id;
          return previous.id;
        end $$
        """,
        f"revoke all on function {_SYNC_SIGNATURE} from public",
        f"grant execute on function {_SYNC_SIGNATURE} to appsell_runtime",
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
            raise exception 'cannot extend schema readiness for 0023'
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
            raise exception 'cannot restore schema readiness for 0023'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        f"drop function {_SYNC_SIGNATURE}",
        """
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit','0023_payout_status_sync')$needle$,
            $replacement$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit')$replacement$
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
            $needle$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit','0023_payout_status_sync')$needle$,
            $replacement$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit')$replacement$
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
