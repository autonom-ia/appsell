"""Migration 0025: reading the caller's own role from the runtime.

This file exists because of a production 403. The payout route used to read
``seller_membership`` directly, and the RLS policy ``membership_self_select``
(0002) admits that read only under the capability allowlist
``audit.read/audit.write/seller.list/seller.read``. Under
``commerce.payout.read`` the runtime saw ZERO rows, so a legitimate owner was
refused on every request.

No test caught it: the API route tests mock the session, and the 0021-0024
Postgres tests exercise the payout FUNCTIONS with a forged context — nothing ever
crossed the RLS boundary under the payout capability. So the first test here is
the regression itself: prove the direct select is still blind, and prove the
function is not.
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

OWNER = UUID("21000000-0000-4000-8000-000000000001")
ADMIN = UUID("21000000-0000-4000-8000-000000000002")
STRANGER = UUID("21000000-0000-4000-8000-000000000003")
SELLER_SLUG = "membership-role-a"
OTHER_SLUG = "membership-role-b"
ISSUER = "https://identity.test.autonomia.site"

PAYOUT_READ = "commerce.payout.read"
SELLER_LIST = "seller.list"
INSUFFICIENT_PRIVILEGE = "42501"


@pytest.fixture(scope="module")
def sellers() -> dict[str, UUID]:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        for identity, sub, email in (
            (OWNER, "membership-owner", "membership.owner@example.test"),
            (ADMIN, "membership-admin", "membership.admin@example.test"),
            (STRANGER, "membership-stranger", "membership.stranger@example.test"),
        ):
            connection.execute(
                "insert into identity_user(id,identity_issuer,identity_sub,"
                "email_normalized,email_verified) values(%s,%s,%s,%s,true) "
                "on conflict do nothing",
                (identity, ISSUER, sub, email),
            )
        created: dict[str, UUID] = {}
        for slug in (SELLER_SLUG, OTHER_SLUG):
            row = connection.execute(
                "insert into seller_organization(slug,display_name,status) "
                "values(%s,%s,'active') on conflict (slug) do update "
                "set display_name=excluded.display_name returning id",
                (slug, f"Membership {slug[-1].upper()}"),
            ).fetchone()
            assert row is not None
            created[slug] = UUID(str(row[0]))
        # The owner owns seller A; the admin administers it; the stranger has no
        # membership anywhere. Seller B exists only so "wrong seller" is a real
        # seller, not a missing row.
        connection.execute(
            "delete from seller_membership where seller_organization_id = any(%s)",
            (list(created.values()),),
        )
        for identity, role in ((OWNER, "seller_owner"), (ADMIN, "seller_admin")):
            connection.execute(
                "insert into seller_membership(seller_organization_id,identity_user_id,"
                "role,status) values(%s,%s,%s,'active')",
                (created[SELLER_SLUG], identity, role),
            )
        connection.commit()
    return created


def _context(
    connection: psycopg.Connection[tuple[object, ...]],
    identity: UUID,
    seller: UUID | None,
    capability: str,
) -> None:
    issued = int(time.time())
    expires = issued + 60
    payload = f"{identity}:{seller or ''}:{capability}:{issued}:{expires}"
    signature = hmac.new(CONTEXT_KEY, payload.encode(), hashlib.sha256).hexdigest()
    connection.execute(
        "select app_private.apply_request_context(%s,%s,%s,%s,%s,%s)",
        (identity, seller, capability, issued, expires, signature),
    )


def _role(connection: psycopg.Connection[tuple[object, ...]], seller: UUID) -> str | None:
    row = connection.execute(
        "select app_private.resolve_seller_membership_role(%s)", (seller,)
    ).fetchone()
    assert row is not None
    return None if row[0] is None else str(row[0])


def test_schema_ready_is_true_after_0025(sellers: dict[str, UUID]) -> None:
    # The readiness gate pins the applied revision; forgetting to widen it makes
    # the API refuse to boot after this migration lands.
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute("select app_private.schema_ready()").fetchone()
    assert row is not None
    assert row[0] is True


def test_direct_select_on_membership_is_still_blind_under_payout_capability(
    sellers: dict[str, UUID],
) -> None:
    # THE REGRESSION. This is what shipped and 403'd in production: the same
    # query the route used to run, under the payout capability, sees nothing.
    # Asserting it stays that way keeps anyone from "fixing" a future 403 by
    # quietly widening the policy instead of using the function.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, OWNER, sellers[SELLER_SLUG], PAYOUT_READ)
        visible = connection.execute("select count(*) from public.seller_membership").fetchone()
        assert visible is not None
        assert visible[0] == 0, (
            "membership_self_select must stay narrow: a direct read under the "
            "payout capability is exactly the bug this migration works around"
        )
        # Same context, same table, but an allowlisted capability: visible. This
        # half proves the zero above is the POLICY talking, not an empty table.
        _context(connection, OWNER, sellers[SELLER_SLUG], SELLER_LIST)
        allowed = connection.execute("select count(*) from public.seller_membership").fetchone()
        assert allowed is not None
        assert allowed[0] == 1


def test_function_reports_the_callers_own_role_under_the_payout_capability(
    sellers: dict[str, UUID],
) -> None:
    # The fix: the definer function answers where the direct select cannot.
    assert RUNTIME_URL
    for identity, expected in ((OWNER, "seller_owner"), (ADMIN, "seller_admin")):
        with psycopg.connect(RUNTIME_URL) as connection:
            _context(connection, identity, sellers[SELLER_SLUG], PAYOUT_READ)
            assert _role(connection, sellers[SELLER_SLUG]) == expected


def test_non_member_gets_null_not_someone_elses_role(sellers: dict[str, UUID]) -> None:
    # Null is how the route decides 403. Returning the owner's role here would
    # hand a stranger the payout surface of a seller they do not belong to.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, STRANGER, sellers[SELLER_SLUG], PAYOUT_READ)
        assert _role(connection, sellers[SELLER_SLUG]) is None


def test_asking_about_another_seller_is_refused_not_answered(
    sellers: dict[str, UUID],
) -> None:
    # Scope mismatch is a caller bug, and answering it would let one request
    # enumerate memberships across tenants. Exact sqlstate, not a bare Error.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, OWNER, sellers[SELLER_SLUG], PAYOUT_READ)
        with pytest.raises(psycopg.Error) as raised:
            _role(connection, sellers[OTHER_SLUG])
        assert raised.value.sqlstate == INSUFFICIENT_PRIVILEGE


def test_inactive_membership_and_inactive_seller_both_read_as_no_role(
    sellers: dict[str, UUID],
) -> None:
    # A revoked member and a suspended seller must both stop reaching the payout
    # surface. Checked one at a time so a pass cannot hide the other.
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(ADMIN_URL) as admin:
        admin.execute(
            "update seller_membership set status='revoked' "
            "where seller_organization_id=%s and identity_user_id=%s",
            (sellers[SELLER_SLUG], ADMIN),
        )
        admin.commit()
        try:
            with psycopg.connect(RUNTIME_URL) as connection:
                _context(connection, ADMIN, sellers[SELLER_SLUG], PAYOUT_READ)
                assert _role(connection, sellers[SELLER_SLUG]) is None
        finally:
            admin.execute(
                "update seller_membership set status='active' "
                "where seller_organization_id=%s and identity_user_id=%s",
                (sellers[SELLER_SLUG], ADMIN),
            )
            admin.commit()

        admin.execute(
            "update seller_organization set status='suspended' where id=%s",
            (sellers[SELLER_SLUG],),
        )
        admin.commit()
        try:
            with psycopg.connect(RUNTIME_URL) as connection:
                _context(connection, OWNER, sellers[SELLER_SLUG], PAYOUT_READ)
                assert _role(connection, sellers[SELLER_SLUG]) is None
        finally:
            admin.execute(
                "update seller_organization set status='active' where id=%s",
                (sellers[SELLER_SLUG],),
            )
            admin.commit()


def test_no_seller_in_context_is_refused(sellers: dict[str, UUID]) -> None:
    # Without a seller in the context the function has no scope to answer within,
    # so it must refuse rather than fall back to "any seller of mine".
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, OWNER, None, PAYOUT_READ)
        with pytest.raises(psycopg.Error) as raised:
            _role(connection, sellers[SELLER_SLUG])
        assert raised.value.sqlstate == INSUFFICIENT_PRIVILEGE


def test_runtime_cannot_reach_the_function_without_a_context(
    sellers: dict[str, UUID],
) -> None:
    # No signed context at all: context_uuid returns null and the gate refuses.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.Error) as raised:
            _role(connection, sellers[SELLER_SLUG])
        assert raised.value.sqlstate == INSUFFICIENT_PRIVILEGE


def test_security_definer_posture(sellers: dict[str, UUID]) -> None:
    # Definer is the whole point (it must outrank the runtime policy), and
    # pg_temp must be LAST in search_path so a temp table cannot shadow
    # public.seller_membership inside the function.
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select p.prosecdef, p.proconfig, "
            "has_function_privilege('appsell_runtime', p.oid, 'execute') "
            "from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
            "where n.nspname='app_private' "
            "and p.proname='resolve_seller_membership_role'"
        ).fetchone()
    assert row is not None
    assert row[0] is True
    assert row[2] is True
    search_path = next(entry for entry in (row[1] or []) if entry.startswith("search_path="))
    assert search_path.endswith("pg_temp"), search_path
