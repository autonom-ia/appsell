"""Migration 0021: the seller-to-payout-account binding.

Each test states the failure mode it catches, because this table decides WHERE A
SALE'S MONEY GOES. A permissive gate here does not corrupt data — it redirects
someone else's revenue.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from uuid import UUID

import psycopg
import pytest

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY = bytes.fromhex(os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX", "") or "")

pytestmark = pytest.mark.skipif(
    not all(
        (
            ADMIN_URL,
            RUNTIME_URL,
            os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX"),
            os.environ.get("APPSELL_TEST_BOOTSTRAP_KEY_HEX"),
        )
    ),
    reason="isolated PostgreSQL environment is required",
)

ACTOR = UUID("20000000-0000-4000-8000-000000000001")
SELLER_A_SLUG = "payout-test-a"
SELLER_B_SLUG = "payout-test-b"
ACCOUNT_A = "acct_test_payout_a"
ACCOUNT_B = "acct_test_payout_b"
READ = "commerce.payout.read"
MANAGE = "commerce.payout.manage"


@pytest.fixture(scope="module")
def sellers() -> dict[str, UUID]:
    # Self-contained tenants: two sellers are required because every meaningful
    # failure mode of this table is cross-tenant.
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "delete from seller_payout_account where provider_account_id in (%s,%s)",
            (ACCOUNT_A, ACCOUNT_B),
        )
        created: dict[str, UUID] = {}
        for slug in (SELLER_A_SLUG, SELLER_B_SLUG):
            row = connection.execute(
                "insert into seller_organization(slug,display_name,status) "
                "values(%s,%s,'active') on conflict (slug) do update "
                "set display_name=excluded.display_name returning id",
                (slug, f"Payout Test {slug[-1].upper()}"),
            ).fetchone()
            assert row is not None
            created[slug] = UUID(str(row[0]))
        connection.commit()
    return created


def _context(
    connection: psycopg.Connection[tuple[object, ...]],
    capability: str,
    seller: UUID | None,
) -> None:
    issued = int(time.time())
    expires = issued + 60
    payload = f"{ACTOR}:{seller or ''}:{capability}:{issued}:{expires}"
    signature = hmac.new(CONTEXT_KEY, payload.encode(), hashlib.sha256).hexdigest()
    connection.execute(
        "select app_private.apply_request_context(%s,%s,%s,%s,%s,%s)",
        (ACTOR, seller, capability, issued, expires, signature),
    )


def _upsert(
    connection: psycopg.Connection[tuple[object, ...]],
    seller: UUID,
    account: str,
    *,
    charges: bool = False,
    payouts: bool = False,
    details: bool = False,
    disabled: str | None = None,
) -> UUID:
    row = connection.execute(
        "select app_private.upsert_seller_payout_account(%s,'stripe',%s,%s,%s,%s,%s)",
        (seller, account, charges, payouts, details, disabled),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def test_schema_ready_is_true_after_0021(sellers: dict[str, UUID]) -> None:
    # Catches the readiness gate not being widened for this revision. Not a
    # cosmetic assertion: schema_ready() gates API boot, so a wrong version_num
    # list means the service refuses to start after the migration lands.
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute("select app_private.schema_ready()").fetchone()
    assert row is not None
    assert row[0] is True


def test_runtime_has_no_direct_table_access(sellers: dict[str, UUID]) -> None:
    # Catches a runtime-facing policy being added by accident. The runtime must
    # only ever see this table through the capability-gated functions; a direct
    # select would expose every seller's payout account to any tenant.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.Error):
            connection.execute("select 1 from seller_payout_account")


def test_upsert_without_capability_is_denied(sellers: dict[str, UUID]) -> None:
    # Catches the capability gate being absent: any authenticated request could
    # then bind or rebind a payout account.
    assert RUNTIME_URL
    seller = sellers[SELLER_A_SLUG]
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, READ, seller)  # read capability, not manage
        with pytest.raises(psycopg.Error):
            _upsert(connection, seller, ACCOUNT_A)


def test_upsert_for_another_seller_is_denied(sellers: dict[str, UUID]) -> None:
    # THE cross-tenant money bug: seller A holding a valid manage capability in
    # its own context must not be able to write a binding for seller B.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, MANAGE, sellers[SELLER_A_SLUG])
        with pytest.raises(psycopg.Error):
            _upsert(connection, sellers[SELLER_B_SLUG], ACCOUNT_B)


def test_upsert_creates_then_refreshes_the_status_cache(sellers: dict[str, UUID]) -> None:
    # Happy path plus the account.updated refresh: the second call must update
    # the same row, never create a second binding for the same seller.
    assert RUNTIME_URL
    seller = sellers[SELLER_A_SLUG]
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, MANAGE, seller)
        created = _upsert(connection, seller, ACCOUNT_A)
        refreshed = _upsert(connection, seller, ACCOUNT_A, charges=True, payouts=True, details=True)
        assert created == refreshed
        connection.commit()

        _context(connection, READ, seller)
        row = connection.execute(
            "select provider_account_id,charges_enabled,payouts_enabled,details_submitted "
            "from app_private.resolve_seller_payout_account(%s,'stripe')",
            (seller,),
        ).fetchone()
    assert row is not None
    assert row[0] == ACCOUNT_A
    assert (row[1], row[2], row[3]) == (True, True, True)


def test_upsert_refuses_repointing_to_a_different_account(sellers: dict[str, UUID]) -> None:
    # Catches silent money redirection: rebinding an existing seller to another
    # provider account would send the next sale somewhere else with no trace.
    assert RUNTIME_URL
    seller = sellers[SELLER_A_SLUG]
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, MANAGE, seller)
        _upsert(connection, seller, ACCOUNT_A)
        connection.commit()
        _context(connection, MANAGE, seller)
        with pytest.raises(psycopg.Error):
            _upsert(connection, seller, "acct_test_payout_hijack")


def test_resolve_for_another_seller_is_denied(sellers: dict[str, UUID]) -> None:
    # Cross-tenant read. The connected-account id alone cannot move money, but
    # it identifies another producer's payment account.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, READ, sellers[SELLER_A_SLUG])
        with pytest.raises(psycopg.Error):
            connection.execute(
                "select 1 from app_private.resolve_seller_payout_account(%s,'stripe')",
                (sellers[SELLER_B_SLUG],),
            )


def test_one_provider_account_cannot_serve_two_sellers(sellers: dict[str, UUID]) -> None:
    # The DB-level tenant guarantee: even with a correct capability in its own
    # context, seller B must not be able to claim the account already bound to
    # seller A — that would route A's sales into B's account.
    assert RUNTIME_URL
    seller_a, seller_b = sellers[SELLER_A_SLUG], sellers[SELLER_B_SLUG]
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, MANAGE, seller_a)
        _upsert(connection, seller_a, ACCOUNT_A)
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, MANAGE, seller_b)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _upsert(connection, seller_b, ACCOUNT_A)
