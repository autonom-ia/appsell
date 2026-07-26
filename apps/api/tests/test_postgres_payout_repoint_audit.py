"""Migration 0022: the caller-side audit of a DENIED payout re-point.

Each test states the failure mode it catches. The function under test writes to
the audit trail of the table that decides WHERE A SALE'S MONEY GOES; a
permissive gate here lets a caller fabricate (or suppress) the record of who
tried to redirect a seller's revenue.

audit_event is append-only (0001's trigger refuses update/delete for everyone),
so no fixture cleans audit rows: every assertion filters by a request_id minted
inside the test, which makes reruns and shared-database suites a non-issue.
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

ACTOR = UUID("21000000-0000-4000-8000-000000000001")
SELLER_A_SLUG = "payout-audit-a"
SELLER_B_SLUG = "payout-audit-b"
SELLER_C_SLUG = "payout-audit-c"
ACCOUNT_A = "acct_repointA"
ACCOUNT_B = "acct_repointB"
INSUFFICIENT_PRIVILEGE = "42501"
RAISE_EXCEPTION = "55000"

READ = "commerce.payout.read"
MANAGE = "commerce.payout.manage"


@pytest.fixture(scope="module")
def sellers() -> dict[str, UUID]:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "delete from public.seller_payout_account where seller_organization_id in "
            "(select id from seller_organization where slug in (%s,%s,%s))",
            (SELLER_A_SLUG, SELLER_B_SLUG, SELLER_C_SLUG),
        )
        # O actor precisa existir em identity_user: audit_event.actor_identity_user_id
        # tem FK e tanto o upsert quanto o record auditam com o id do contexto.
        connection.execute(
            "insert into identity_user(id,identity_issuer,identity_sub,"
            "email_normalized,email_verified) "
            "values(%s,'https://identity.test.autonomia.site','payout-audit-actor',"
            "'payout.audit.actor@example.test',true) on conflict do nothing",
            (ACTOR,),
        )
        created: dict[str, UUID] = {}
        for slug in (SELLER_A_SLUG, SELLER_B_SLUG, SELLER_C_SLUG):
            row = connection.execute(
                "insert into seller_organization(slug,display_name,status) "
                "values(%s,%s,'active') on conflict (slug) do update "
                "set display_name=excluded.display_name returning id",
                (slug, f"Payout Audit {slug[-1].upper()}"),
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


def _bind(connection: psycopg.Connection[tuple[object, ...]], seller: UUID, account: str) -> UUID:
    row = connection.execute(
        "select app_private.upsert_seller_payout_account(%s,'stripe',%s,false,false,false,null,%s)",
        (seller, account, uuid4()),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def test_schema_ready_is_true_after_0022(sellers: dict[str, UUID]) -> None:
    # Catches the readiness gate not being widened for this revision:
    # schema_ready() gates API boot, so a wrong version_num list means the
    # service refuses to start after the migration lands.
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute("select app_private.schema_ready()").fetchone()
    assert row is not None
    assert row[0] is True


def test_denied_repoint_is_recorded_with_the_bound_and_attempted_accounts(
    sellers: dict[str, UUID],
) -> None:
    # The full caller dance: bind, get refused on a re-point, record the denial
    # in a fresh transaction (mirroring the route's savepoint + commit), and
    # prove the audit row carries WHO tried to point WHICH seller at WHAT
    # account — with the denial, not a success, as the result.
    assert RUNTIME_URL and ADMIN_URL
    seller = sellers[SELLER_A_SLUG]
    request_id = uuid4()
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, MANAGE, seller)
        binding_id = _bind(connection, seller, ACCOUNT_A)
        connection.commit()

        _context(connection, MANAGE, seller)
        with pytest.raises(psycopg.Error) as refusal:
            connection.execute(
                "select app_private.upsert_seller_payout_account(%s,'stripe',%s,"
                "false,false,false,null,%s)",
                (seller, ACCOUNT_B, uuid4()),
            )
        assert refusal.value.sqlstate == RAISE_EXCEPTION
        connection.rollback()

        _context(connection, MANAGE, seller)
        row = connection.execute(
            "select app_private.record_payout_repoint_denied(%s,'stripe',%s,%s)",
            (seller, ACCOUNT_B, request_id),
        ).fetchone()
        assert row is not None
        connection.commit()

    with psycopg.connect(ADMIN_URL) as connection:
        audit = connection.execute(
            "select action, result, target_type, target_id, actor_identity_user_id, "
            "seller_organization_id, metadata from public.audit_event "
            "where request_id = %s",
            (request_id,),
        ).fetchall()
    assert len(audit) == 1
    action, result, target_type, target_id, actor, audited_seller, metadata = audit[0]
    assert action == "payout_account.repoint"
    assert result == "denied"
    assert target_type == "seller_payout_account"
    assert UUID(str(target_id)) == binding_id
    assert UUID(str(actor)) == ACTOR
    assert UUID(str(audited_seller)) == seller
    assert metadata == {
        "provider": "stripe",
        "attempted_provider_account_id": ACCOUNT_B,
        "bound_provider_account_id": ACCOUNT_A,
    }


def test_recording_requires_the_manage_capability(sellers: dict[str, UUID]) -> None:
    # Catches the audit writer accepting a weaker capability. A caller that can
    # only READ payout state must not be able to write denial records about it.
    assert RUNTIME_URL
    seller = sellers[SELLER_A_SLUG]
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, READ, seller)
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
            connection.execute(
                "select app_private.record_payout_repoint_denied(%s,'stripe',%s,%s)",
                (seller, ACCOUNT_B, uuid4()),
            )
        assert denied.value.sqlstate == INSUFFICIENT_PRIVILEGE


def test_recording_requires_the_matching_tenant_context(
    sellers: dict[str, UUID],
) -> None:
    # Catches the cross-tenant hole: seller B's context must not be able to
    # write denial records into seller A's audit trail.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, MANAGE, sellers[SELLER_B_SLUG])
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
            connection.execute(
                "select app_private.record_payout_repoint_denied(%s,'stripe',%s,%s)",
                (sellers[SELLER_A_SLUG], ACCOUNT_B, uuid4()),
            )
        assert denied.value.sqlstate == INSUFFICIENT_PRIVILEGE


def test_recording_requires_a_request_id(sellers: dict[str, UUID]) -> None:
    # request_id is the join key between the audit row and the request that
    # produced it; an audit row without one is unattributable noise.
    assert RUNTIME_URL
    seller = sellers[SELLER_A_SLUG]
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, MANAGE, seller)
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
            connection.execute(
                "select app_private.record_payout_repoint_denied(%s,'stripe',%s,null)",
                (seller, ACCOUNT_B),
            )
        assert denied.value.sqlstate == INSUFFICIENT_PRIVILEGE


def test_recording_refuses_when_no_binding_exists(sellers: dict[str, UUID]) -> None:
    # A denial only exists relative to an existing binding. Without this, a
    # caller could flood a virgin seller's audit trail with fabricated
    # "someone tried to re-point" rows before any account was ever bound.
    assert RUNTIME_URL
    seller = sellers[SELLER_C_SLUG]
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, MANAGE, seller)
        with pytest.raises(psycopg.Error) as refusal:
            connection.execute(
                "select app_private.record_payout_repoint_denied(%s,'stripe',%s,%s)",
                (seller, ACCOUNT_B, uuid4()),
            )
        assert refusal.value.sqlstate == RAISE_EXCEPTION


def test_function_posture_is_security_definer_with_pinned_search_path(
    sellers: dict[str, UUID],
) -> None:
    # Catches the pg_temp shadowing class (#158) being reintroduced: the
    # function must be security definer AND pin a search_path that ends with
    # pg_temp, so a hostile temp table cannot shadow audit_event.
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select prosecdef, proconfig from pg_proc "
            "where oid = 'app_private.record_payout_repoint_denied(uuid,text,text,uuid)'"
            "::regprocedure"
        ).fetchone()
    assert row is not None
    prosecdef, proconfig = row
    assert prosecdef is True
    search_path = next(entry for entry in proconfig if entry.startswith("search_path="))
    assert search_path.endswith("pg_temp")
