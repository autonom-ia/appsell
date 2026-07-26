"""Resolve WHERE a seller's sale settles, for the checkout worker.

The buyer creating a checkout session is not a member of the seller — the
tenant-gated read (0021's resolve, member-only via the payout routes) can never
serve the charge path. This function is the charge path's single question:
"which provider account receives this seller's money, and can it charge yet?".

Worker-shaped gate, same inversion as 0023: the dedicated capability AND no
tenant in context. It returns exactly two fields — the account id and
charges_enabled — because that is all a charge needs; the rest of the cache
(payouts_enabled, disabled_reason, timestamps) stays behind the member-only
read. No binding → zero rows, and the caller refuses to charge: a sale must
never fall back to the platform's own account by accident, that is the custody
model #143 forbids.
"""

from __future__ import annotations

from alembic import op

revision = "0024_payout_destination"
down_revision = "0023_payout_status_sync"
branch_labels = None
depends_on = None

_DESTINATION_SIGNATURE = "app_private.resolve_payout_destination(uuid,text)"

_READY_ANCHOR = "select app_private.schema_ready_base_0017()"
_READY_ADDITION = """select app_private.schema_ready_base_0017()
            and to_regprocedure(
              'app_private.resolve_payout_destination(uuid,text)'
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
            $needle$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit','0023_payout_status_sync')$needle$,
            $replacement$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit','0023_payout_status_sync','0024_payout_destination')$replacement$
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
            $needle$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit','0023_payout_status_sync')$needle$,
            $replacement$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit','0023_payout_status_sync','0024_payout_destination')$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0017 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        """
        create function app_private.resolve_payout_destination(
          p_seller_organization_id uuid, p_provider text
        )
        returns table(provider_account_id text, charges_enabled boolean)
        language plpgsql stable security definer
        set search_path=pg_catalog,app_private,public,pg_temp as $$
        begin
          if app_private.context_text('capability')
               is distinct from 'commerce.checkout.destination'
            or app_private.context_uuid('seller_organization_id') is not null
            or p_seller_organization_id is null
          then
            raise exception 'payout destination read denied' using errcode = '42501';
          end if;
          return query
            select account.provider_account_id, account.charges_enabled
            from public.seller_payout_account account
            where account.seller_organization_id = p_seller_organization_id
              and account.provider = p_provider;
        end $$
        """,
        f"revoke all on function {_DESTINATION_SIGNATURE} from public",
        f"grant execute on function {_DESTINATION_SIGNATURE} to appsell_runtime",
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
            raise exception 'cannot extend schema readiness for 0024'
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
            raise exception 'cannot restore schema readiness for 0024'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        f"drop function {_DESTINATION_SIGNATURE}",
        """
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit','0023_payout_status_sync','0024_payout_destination')$needle$,
            $replacement$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit','0023_payout_status_sync')$replacement$
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
            $needle$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit','0023_payout_status_sync','0024_payout_destination')$needle$,
            $replacement$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit','0023_payout_status_sync')$replacement$
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
