"""Migration 0024: the checkout worker's payout-destination lookup.

The function answers one question for the charge path — which account receives
the seller's money and can it charge — under a worker-shaped gate. A permissive
gate here leaks every seller's account id to any capability; a missing row
mishandled by the caller charges the PLATFORM instead of the producer.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from uuid import UUID, uuid4

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

ACTOR = UUID("23000000-0000-4000-8000-000000000001")
SELLER_SLUG = "payout-dest-a"
EMPTY_SELLER_SLUG = "payout-dest-b"
ACCOUNT = "acct_destTargetA"
INSUFFICIENT_PRIVILEGE = "42501"

DESTINATION = "commerce.checkout.destination"
MANAGE = "commerce.payout.manage"


@pytest.fixture(scope="module")
def sellers() -> dict[str, UUID]:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "delete from public.seller_payout_account where seller_organization_id in "
            "(select id from seller_organization where slug in (%s,%s))",
            (SELLER_SLUG, EMPTY_SELLER_SLUG),
        )
        connection.execute(
            "insert into identity_user(id,identity_issuer,identity_sub,"
            "email_normalized,email_verified) "
            "values(%s,'https://identity.test.autonomia.site','payout-dest-actor',"
            "'payout.dest.actor@example.test',true) on conflict do nothing",
            (ACTOR,),
        )
        created: dict[str, UUID] = {}
        for slug in (SELLER_SLUG, EMPTY_SELLER_SLUG):
            row = connection.execute(
                "insert into seller_organization(slug,display_name,status) "
                "values(%s,%s,'active') on conflict (slug) do update "
                "set display_name=excluded.display_name returning id",
                (slug, f"Payout Dest {slug[-1].upper()}"),
            ).fetchone()
            assert row is not None
            created[slug] = UUID(str(row[0]))
        connection.commit()
    return created


def _context(
    connection: psycopg.Connection[tuple[object, ...]],
    capability: str,
    seller_id: UUID | None,
) -> None:
    issued = int(time.time())
    expires = issued + 60
    payload = f"{ACTOR}:{seller_id or ''}:{capability}:{issued}:{expires}"
    signature = hmac.new(CONTEXT_KEY, payload.encode(), hashlib.sha256).hexdigest()
    connection.execute(
        "select app_private.apply_request_context(%s,%s,%s,%s,%s,%s)",
        (ACTOR, seller_id, capability, issued, expires, signature),
    )


def test_schema_ready_is_true_after_0024(sellers: dict[str, UUID]) -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute("select app_private.schema_ready()").fetchone()
    assert row is not None
    assert row[0] is True


def test_destination_returns_the_bound_account_and_its_charge_readiness(
    sellers: dict[str, UUID],
) -> None:
    assert RUNTIME_URL
    seller = sellers[SELLER_SLUG]
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, MANAGE, seller)
        connection.execute(
            "select app_private.upsert_seller_payout_account(%s,'stripe',%s,"
            "true,false,true,null,%s)",
            (seller, ACCOUNT, uuid4()),
        )
        connection.commit()
        _context(connection, DESTINATION, None)
        row = connection.execute(
            "select provider_account_id, charges_enabled "
            "from app_private.resolve_payout_destination(%s,'stripe')",
            (seller,),
        ).fetchone()
    assert row == (ACCOUNT, True)


def test_destination_is_empty_for_a_seller_with_no_binding(
    sellers: dict[str, UUID],
) -> None:
    # The caller must refuse to charge on an empty result — this test pins the
    # "empty means no destination" contract that decision depends on.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, DESTINATION, None)
        rows = connection.execute(
            "select * from app_private.resolve_payout_destination(%s,'stripe')",
            (sellers[EMPTY_SELLER_SLUG],),
        ).fetchall()
    assert rows == []


def test_destination_refuses_other_capabilities(sellers: dict[str, UUID]) -> None:
    # The member-facing manage capability must not double as a cross-tenant
    # account-id oracle.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, MANAGE, sellers[SELLER_SLUG])
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
            connection.execute(
                "select * from app_private.resolve_payout_destination(%s,'stripe')",
                (sellers[SELLER_SLUG],),
            )
        assert denied.value.sqlstate == INSUFFICIENT_PRIVILEGE


def test_destination_refuses_a_tenant_scoped_context(sellers: dict[str, UUID]) -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, DESTINATION, sellers[SELLER_SLUG])
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
            connection.execute(
                "select * from app_private.resolve_payout_destination(%s,'stripe')",
                (sellers[SELLER_SLUG],),
            )
        assert denied.value.sqlstate == INSUFFICIENT_PRIVILEGE


def test_function_posture_is_security_definer_with_pinned_search_path(
    sellers: dict[str, UUID],
) -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select prosecdef, proconfig from pg_proc where oid = "
            "'app_private.resolve_payout_destination(uuid,text)'::regprocedure"
        ).fetchone()
    assert row is not None
    prosecdef, proconfig = row
    assert prosecdef is True
    search_path = next(entry for entry in proconfig if entry.startswith("search_path="))
    assert search_path.endswith("pg_temp")
