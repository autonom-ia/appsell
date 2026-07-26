"""Audit the DENIED attempt to re-point a seller's payout account.

0021's upsert refuses with 55000 when a caller tries to bind a seller to a
DIFFERENT provider account than the one already bound. That refusal is the most
interesting security signal this table produces — someone tried to change where
a seller's money goes — and it cannot be audited inside the function that
raises it: the exception rolls back the function's own audit insert, and
PostgreSQL has no autonomous transactions.

So the caller audits it. This revision gives the caller the one tool it needs:
a ``security definer`` function that records the denied attempt under the SAME
capability and tenant gate as the write it mirrors, so a caller that cannot
perform the write cannot fabricate denials about it either. The API route
wraps the upsert in a savepoint; on 55000 it rolls the savepoint back (safe:
the denial path writes nothing) and records the denial, which then commits with
the request's outer transaction.

The denial is tied to the EXISTING binding: ``target_id`` is the bound row's id
and the metadata carries both the bound and the attempted provider account. A
seller with no binding cannot accumulate "denied re-point" noise — the function
refuses instead, because a denial only exists relative to something to deny.
"""

from __future__ import annotations

from alembic import op

revision = "0022_payout_repoint_audit"
down_revision = "0021_seller_payout_account"
branch_labels = None
depends_on = None

_RECORD_SIGNATURE = "app_private.record_payout_repoint_denied(uuid,text,text,uuid)"

# Readiness assertion appended to schema_ready(). Anchored on the base call the
# accumulated list from 0018-0021 starts with, so the earlier assertions are
# preserved instead of being retyped (and silently truncated).
_READY_ANCHOR = "select app_private.schema_ready_base_0017()"
_READY_ADDITION = """select app_private.schema_ready_base_0017()
            and to_regprocedure(
              'app_private.record_payout_repoint_denied(uuid,text,text,uuid)'
            ) is not null"""


def upgrade() -> None:
    statements = [
        # The readiness gate pins the exact set of applied revisions. Leaving
        # 0022 out of it makes schema_ready() return false once this migration
        # lands, and the API refuses to boot. Widening it is not optional.
        """
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0016()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account')$needle$,
            $replacement$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit')$replacement$
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
            $needle$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account')$needle$,
            $replacement$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit')$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0017 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        """
        create function app_private.record_payout_repoint_denied(
          p_seller_organization_id uuid, p_provider text,
          p_attempted_provider_account_id text, p_request_id uuid
        )
        returns uuid language plpgsql volatile security definer
        set search_path=pg_catalog,app_private,public,pg_temp as $$
        declare audit_id uuid; actor uuid;
        begin
          if app_private.context_text('capability')
               is distinct from 'commerce.payout.manage'
            or p_seller_organization_id is null
            or app_private.context_uuid('seller_organization_id')
               is distinct from p_seller_organization_id
            or p_request_id is null
          then
            raise exception 'payout audit write denied' using errcode = '42501';
          end if;
          actor := app_private.context_uuid('identity_user_id');
          insert into public.audit_event(
            seller_organization_id, actor_identity_user_id, action, target_type,
            target_id, result, request_id, metadata
          )
          select p_seller_organization_id, actor, 'payout_account.repoint',
                 'seller_payout_account', account.id, 'denied', p_request_id,
                 jsonb_build_object(
                   'provider', p_provider,
                   'attempted_provider_account_id', p_attempted_provider_account_id,
                   'bound_provider_account_id', account.provider_account_id
                 )
          from public.seller_payout_account account
          where account.seller_organization_id = p_seller_organization_id
            and account.provider = p_provider
          returning id into audit_id;
          if audit_id is null then
            raise exception
              'no payout account bound for this seller and provider'
              using errcode = '55000';
          end if;
          return audit_id;
        end $$
        """,
        f"revoke all on function {_RECORD_SIGNATURE} from public",
        f"grant execute on function {_RECORD_SIGNATURE} to appsell_runtime",
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
            raise exception 'cannot extend schema readiness for 0022'
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
            raise exception 'cannot restore schema readiness for 0022'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        f"drop function {_RECORD_SIGNATURE}",
        """
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit')$needle$,
            $replacement$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account')$replacement$
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
            $needle$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account','0022_payout_repoint_audit')$needle$,
            $replacement$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account')$replacement$
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
