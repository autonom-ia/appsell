"""Bind a seller organization to the payout account that receives its sales.

Trilho B (#143, ADR 0006): the buyer pays the PRODUCER, not the platform. That
requires knowing, per seller, which provider account the money settles into, and
whether that account is actually able to take a charge yet.

Provider-agnostic on purpose. The columns are ``provider`` + ``provider_account_id``
rather than ``stripe_account_id`` because the gateway is an implementation
detail: the product surface never names it (the producer clicks "Conectar conta
Autonom.ia"), and swapping or adding a provider must not require a table
rewrite. Today the check constraint admits only ``'stripe'``; widening it is a
one-line migration.

Two uniqueness guarantees:
``(seller_organization_id, provider)`` — one payout account per seller per
provider, so a sale can never be ambiguous about its destination.
``(provider, provider_account_id)`` — one provider account is never attached to
two sellers.

Being precise about what that second one does and does not buy, because the first
draft of this comment overstated it: what actually stops seller A from routing
seller B's money is the TENANT GATE in the functions below — A cannot write a row
for B at all. The constraint is defence in depth against a duplicate binding, and
it does NOT prove that the account belongs to the seller claiming it. It also
cuts the other way: A claiming a leaked, not-yet-persisted account id of B would
deny B's onboarding. The real control for that lives in the next slice: the
account id must be taken ONLY from the authenticated ``accounts.create``
response, never from client input.

Access follows the 0012 shape: RLS enabled AND forced, an owner-only policy, and
NO runtime-facing policy. The runtime reaches the table exclusively through
``security definer`` functions that assert both the capability and that the
caller's tenant context matches the seller being read or written. A leaked
connected-account id is not enough to move anyone's money, but it is a
cross-tenant information leak, so the read is gated too.

The status columns are a CACHE of the provider's truth, refreshed from the
``account.updated`` webhook. They are never the authority for "can this seller
sell": they exist so a page render does not need a provider round-trip. The
charge path re-reads them, and the provider refuses the charge regardless if the
account is not ready.

Downgrade is refused while any payout account row exists: a connected account is
real-world state (a producer completed identity verification against it) and
dropping the binding would silently orphan it while the provider still holds it.
"""

from __future__ import annotations

from alembic import op

revision = "0021_seller_payout_account"
down_revision = "0020_checkout_fulfillment"
branch_labels = None
depends_on = None

_RESOLVE_SIGNATURE = "app_private.resolve_seller_payout_account(uuid,text)"
_UPSERT_SIGNATURE = (
    "app_private.upsert_seller_payout_account(uuid,text,text,boolean,boolean,boolean,text)"
)

# Readiness assertion appended to schema_ready(). Anchored on the base call that
# every revision since 0018 starts from, so the accumulated assertion list from
# 0018/0019/0020 is preserved instead of being retyped (and silently truncated).
_READY_ANCHOR = "select app_private.schema_ready_base_0017()"
_READY_ADDITION = """select app_private.schema_ready_base_0017()
            and to_regclass('public.seller_payout_account') is not null
            and to_regprocedure('app_private.resolve_seller_payout_account(uuid,text)') is not null
            and to_regprocedure(
              'app_private.upsert_seller_payout_account(uuid,text,text,boolean,boolean,boolean,text)'
            ) is not null"""


def upgrade() -> None:
    statements = [
        # The readiness gate pins the exact set of applied revisions. Leaving
        # 0021 out of it makes schema_ready() return false once this migration
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
            $needle$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment')$needle$,
            $replacement$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account')$replacement$
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
            $needle$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment')$needle$,
            $replacement$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account')$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0017 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        """
        create table public.seller_payout_account (
          id uuid primary key default gen_random_uuid(),
          seller_organization_id uuid not null references seller_organization(id),
          provider text not null check (provider in ('stripe')),
          provider_account_id text not null check (
            provider_account_id = btrim(provider_account_id)
            and length(provider_account_id) between 3 and 255
            and provider_account_id !~ '[[:cntrl:][:space:]]'
          ),
          charges_enabled boolean not null default false,
          payouts_enabled boolean not null default false,
          details_submitted boolean not null default false,
          disabled_reason text check (
            disabled_reason is null or (
              disabled_reason = btrim(disabled_reason)
              and length(disabled_reason) between 1 and 255
              and disabled_reason !~ '[[:cntrl:]]'
            )
          ),
          status_synced_at timestamptz not null default clock_timestamp(),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          unique (seller_organization_id, provider),
          unique (provider, provider_account_id)
        )
        """,
        "alter table public.seller_payout_account enable row level security",
        "alter table public.seller_payout_account force row level security",
        """
        create policy seller_payout_account_owner_all on public.seller_payout_account
          to appsell_owner using (true) with check (true)
        """,
        # Read path. Returns the cached provider status for ONE seller, and only
        # when the caller's tenant context is that same seller.
        """
        create function app_private.resolve_seller_payout_account(
          p_seller_organization_id uuid, p_provider text
        )
        returns table(
          payout_account_id uuid, provider_account_id text,
          charges_enabled boolean, payouts_enabled boolean,
          details_submitted boolean, disabled_reason text,
          status_synced_at timestamptz
        ) language plpgsql stable security definer
        set search_path=pg_catalog,app_private,public,pg_temp as $$
        begin
          if app_private.context_text('capability') is distinct from 'commerce.payout.read'
            or p_seller_organization_id is null
            or app_private.context_uuid('seller_organization_id')
               is distinct from p_seller_organization_id
          then
            raise exception 'payout account read denied' using errcode = '42501';
          end if;
          return query
            select account.id, account.provider_account_id,
                   account.charges_enabled, account.payouts_enabled,
                   account.details_submitted, account.disabled_reason,
                   account.status_synced_at
            from public.seller_payout_account account
            where account.seller_organization_id = p_seller_organization_id
              and account.provider = p_provider;
        end $$
        """,
        # Write path. Creates the binding on first connect and refreshes the
        # status cache afterwards. provider_account_id is immutable once set: a
        # seller re-pointing at a different account would silently redirect
        # money, so that is a deliberate operational action, not an upsert.
        """
        create function app_private.upsert_seller_payout_account(
          p_seller_organization_id uuid, p_provider text, p_provider_account_id text,
          p_charges_enabled boolean, p_payouts_enabled boolean,
          p_details_submitted boolean, p_disabled_reason text
        )
        returns uuid language plpgsql volatile security definer
        set search_path=pg_catalog,app_private,public,pg_temp as $$
        declare existing_account_id uuid; existing_provider_account_id text;
        begin
          if app_private.context_text('capability') is distinct from 'commerce.payout.manage'
            or p_seller_organization_id is null
            or app_private.context_uuid('seller_organization_id')
               is distinct from p_seller_organization_id
          then
            raise exception 'payout account write denied' using errcode = '42501';
          end if;
          -- Serializa por seller ANTES de olhar a tabela. `for update` sozinho nao
          -- resolve a corrida de PRIMEIRA gravacao: sem linha nao ha o que travar,
          -- e dois onboardings simultaneos do mesmo seller (retry do produtor
          -- concorrendo com o webhook account.updated) chegariam os dois ao insert,
          -- e um levaria 23505 num caso que deveria ser idempotente.
          perform pg_advisory_xact_lock(
            hashtextextended('seller_payout_account', 0),
            hashtextextended(p_seller_organization_id::text || '/' || p_provider, 0)
          );
          select account.id, account.provider_account_id
            into existing_account_id, existing_provider_account_id
            from public.seller_payout_account account
            where account.seller_organization_id = p_seller_organization_id
              and account.provider = p_provider
            for update;
          if existing_account_id is null then
            insert into public.seller_payout_account(
              seller_organization_id, provider, provider_account_id,
              charges_enabled, payouts_enabled, details_submitted, disabled_reason
            ) values (
              p_seller_organization_id, p_provider, p_provider_account_id,
              coalesce(p_charges_enabled, false), coalesce(p_payouts_enabled, false),
              coalesce(p_details_submitted, false), p_disabled_reason
            ) returning id into existing_account_id;
            return existing_account_id;
          end if;
          if existing_provider_account_id is distinct from p_provider_account_id then
            raise exception
              'payout account already bound to a different provider account'
              using errcode = '55000';
          end if;
          update public.seller_payout_account
            set charges_enabled = coalesce(p_charges_enabled, charges_enabled),
                payouts_enabled = coalesce(p_payouts_enabled, payouts_enabled),
                details_submitted = coalesce(p_details_submitted, details_submitted),
                disabled_reason = p_disabled_reason,
                status_synced_at = clock_timestamp(),
                updated_at = clock_timestamp()
            where id = existing_account_id;
          return existing_account_id;
        end $$
        """,
        f"revoke all on function {_RESOLVE_SIGNATURE} from public",
        f"revoke all on function {_UPSERT_SIGNATURE} from public",
        f"grant execute on function {_RESOLVE_SIGNATURE} to appsell_runtime",
        f"grant execute on function {_UPSERT_SIGNATURE} to appsell_runtime",
        # Append the new assertions to the accumulated readiness definition
        # instead of retyping 0018/0019/0020's list.
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
            raise exception 'cannot extend schema readiness for 0021'
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
        """
        do $migration$
        begin
          -- Um payout account existente significa que um produtor concluiu
          -- verificacao de identidade contra aquela conta no provedor. Derrubar
          -- o vinculo aqui deixaria a conta orfa la, e a proxima venda desse
          -- seller nao saberia para onde mandar o dinheiro.
          if exists (select 1 from public.seller_payout_account) then
            raise exception
              'cannot downgrade 0021_seller_payout_account: payout accounts exist; the provider binding must be preserved'
              using errcode = '55000';
          end if;
        end $migration$
        """,
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
            raise exception 'cannot restore schema readiness for 0021'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        f"drop function {_UPSERT_SIGNATURE}",
        f"drop function {_RESOLVE_SIGNATURE}",
        "drop table public.seller_payout_account",
        """
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account')$needle$,
            $replacement$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment')$replacement$
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
            $needle$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment','0021_seller_payout_account')$needle$,
            $replacement$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment')$replacement$
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
