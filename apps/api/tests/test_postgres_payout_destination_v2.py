"""Migration 0073: o destino do pagamento diz também se a conta repassa (#545).

A loja que cobra na própria conta só vende com a conta verificada
(`charges_enabled` e `payouts_enabled`). A v2 tem de devolver os dois flags,
com a mesma trava de capacidade da 0024, e acompanhar a conta que perde a
verificação depois de ativa.
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

ACTOR = UUID("23000000-0000-4000-8000-000000000073")
SELLER_SLUG = "payout-dest-v2-a"
EMPTY_SELLER_SLUG = "payout-dest-v2-b"
ACCOUNT = "acct_destV2TargetA"
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
            "values(%s,'https://identity.test.autonomia.site','payout-dest-v2-actor',"
            "'payout.dest.v2.actor@example.test',true) on conflict do nothing",
            (ACTOR,),
        )
        created: dict[str, UUID] = {}
        for slug in (SELLER_SLUG, EMPTY_SELLER_SLUG):
            row = connection.execute(
                "insert into seller_organization(slug,display_name,status) "
                "values(%s,%s,'active') on conflict (slug) do update "
                "set display_name=excluded.display_name returning id",
                (slug, f"Payout Dest V2 {slug[-1].upper()}"),
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


def _upsert(
    connection: psycopg.Connection[tuple[object, ...]],
    seller: UUID,
    *,
    charges: bool,
    payouts: bool,
) -> None:
    _context(connection, MANAGE, seller)
    connection.execute(
        "select app_private.upsert_seller_payout_account(%s,'stripe',%s,%s,%s,true,null,%s)",
        (seller, ACCOUNT, charges, payouts, uuid4()),
    )
    connection.commit()


def _destination(
    connection: psycopg.Connection[tuple[object, ...]], seller: UUID
) -> list[tuple[object, ...]]:
    _context(connection, DESTINATION, None)
    return connection.execute(
        "select provider_account_id, charges_enabled, payouts_enabled "
        "from app_private.resolve_payout_destination_v2(%s,'stripe')",
        (seller,),
    ).fetchall()


def test_schema_ready_is_true_after_0073(sellers: dict[str, UUID]) -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute("select app_private.schema_ready()").fetchone()
    assert row is not None
    assert row[0] is True


def test_v2_returns_both_flags_and_follows_a_lost_verification(
    sellers: dict[str, UUID],
) -> None:
    # Catches: a store that can charge but not pay out being treated as ready,
    # and a store that loses verification after activation still selling.
    assert RUNTIME_URL
    seller = sellers[SELLER_SLUG]
    with psycopg.connect(RUNTIME_URL) as connection:
        _upsert(connection, seller, charges=True, payouts=False)
        assert _destination(connection, seller) == [(ACCOUNT, True, False)]
        _upsert(connection, seller, charges=True, payouts=True)
        assert _destination(connection, seller) == [(ACCOUNT, True, True)]
        _upsert(connection, seller, charges=False, payouts=True)
        assert _destination(connection, seller) == [(ACCOUNT, False, True)]


def test_v2_is_empty_for_a_seller_with_no_binding(sellers: dict[str, UUID]) -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        assert _destination(connection, sellers[EMPTY_SELLER_SLUG]) == []


@pytest.mark.parametrize("scoped", [False, True])
def test_v2_refuses_other_capabilities_and_tenant_scoped_contexts(
    sellers: dict[str, UUID], scoped: bool
) -> None:
    # Catches: the v2 becoming a cross-tenant account-id oracle.
    assert RUNTIME_URL
    seller = sellers[SELLER_SLUG]
    with psycopg.connect(RUNTIME_URL) as connection:
        if scoped:
            _context(connection, DESTINATION, seller)
        else:
            _context(connection, MANAGE, seller)
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
            connection.execute(
                "select * from app_private.resolve_payout_destination_v2(%s,'stripe')",
                (seller,),
            )
        assert denied.value.sqlstate == INSUFFICIENT_PRIVILEGE


def test_v2_posture_is_security_definer_owned_by_appsell_owner(
    sellers: dict[str, UUID],
) -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select prosecdef, proconfig, pg_get_userbyid(proowner), "
            "has_function_privilege('public', oid, 'execute'), "
            "has_function_privilege('appsell_runtime', oid, 'execute') "
            "from pg_proc where oid = "
            "'app_private.resolve_payout_destination_v2(uuid,text)'::regprocedure"
        ).fetchone()
    assert row is not None
    prosecdef, proconfig, owner, public_can, runtime_can = row
    assert prosecdef is True
    assert owner == "appsell_owner"
    assert public_can is False
    assert runtime_can is True
    search_path = next(entry for entry in proconfig if entry.startswith("search_path="))
    assert search_path.endswith("pg_temp")
