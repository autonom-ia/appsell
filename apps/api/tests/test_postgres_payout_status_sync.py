"""Migration 0023: provider-driven refresh of the payout status cache.

Each test states the failure mode it catches. The sync function is reachable
with NO tenant context — the exact opposite of every other payout write — so
the gates that keep it from becoming a cross-tenant write primitive are the
whole point of this suite.
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

ACTOR = UUID("22000000-0000-4000-8000-000000000001")
SELLER_SLUG = "payout-sync-a"
ACCOUNT = "acct_syncTargetA"
UNKNOWN_ACCOUNT = "acct_syncUnknown"
INSUFFICIENT_PRIVILEGE = "42501"

SYNC = "commerce.payout.sync"
MANAGE = "commerce.payout.manage"


@pytest.fixture(scope="module")
def seller() -> UUID:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "delete from public.seller_payout_account where seller_organization_id in "
            "(select id from seller_organization where slug = %s)",
            (SELLER_SLUG,),
        )
        connection.execute(
            "insert into identity_user(id,identity_issuer,identity_sub,"
            "email_normalized,email_verified) "
            "values(%s,'https://identity.test.autonomia.site','payout-sync-actor',"
            "'payout.sync.actor@example.test',true) on conflict do nothing",
            (ACTOR,),
        )
        row = connection.execute(
            "insert into seller_organization(slug,display_name,status) "
            "values(%s,'Payout Sync A','active') on conflict (slug) do update "
            "set display_name=excluded.display_name returning id",
            (SELLER_SLUG,),
        ).fetchone()
        assert row is not None
        connection.commit()
        return UUID(str(row[0]))


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


def _bind(connection: psycopg.Connection[tuple[object, ...]], seller_id: UUID) -> UUID:
    _context(connection, MANAGE, seller_id)
    row = connection.execute(
        "select app_private.upsert_seller_payout_account(%s,'stripe',%s,false,false,false,null,%s)",
        (seller_id, ACCOUNT, uuid4()),
    ).fetchone()
    assert row is not None
    connection.commit()
    return UUID(str(row[0]))


def _sync(
    connection: psycopg.Connection[tuple[object, ...]],
    account: str,
    request_id: UUID,
    *,
    charges: bool = True,
    payouts: bool = True,
    details: bool = True,
    disabled: str | None = None,
) -> UUID | None:
    row = connection.execute(
        "select app_private.sync_payout_account_status('stripe',%s,%s,%s,%s,%s,%s)",
        (account, charges, payouts, details, disabled, request_id),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0])) if row[0] is not None else None


def test_schema_ready_is_true_after_0023(seller: UUID) -> None:
    # A wrong version_num list makes schema_ready() false and the API refuses
    # to boot after this migration lands.
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute("select app_private.schema_ready()").fetchone()
    assert row is not None
    assert row[0] is True


def test_provider_update_refreshes_the_cache_and_audits_with_no_actor(
    seller: UUID,
) -> None:
    # The full webhook dance: bind (user context), then sync (worker context,
    # no tenant). The audit row must carry a NULL actor and source=provider —
    # attributing a provider webhook to a human would fabricate evidence.
    assert RUNTIME_URL and ADMIN_URL
    request_id = uuid4()
    with psycopg.connect(RUNTIME_URL) as connection:
        binding_id = _bind(connection, seller)
        _context(connection, SYNC, None)
        synced = _sync(connection, ACCOUNT, request_id, disabled=None)
        connection.commit()
    assert synced == binding_id

    with psycopg.connect(ADMIN_URL) as connection:
        audit = connection.execute(
            "select actor_identity_user_id, action, result, metadata "
            "from public.audit_event where request_id = %s",
            (request_id,),
        ).fetchall()
        cache = connection.execute(
            "select charges_enabled, payouts_enabled, details_submitted "
            "from public.seller_payout_account where id = %s",
            (binding_id,),
        ).fetchone()
    assert cache == (True, True, True)
    assert len(audit) == 1
    actor, action, result, metadata = audit[0]
    assert actor is None
    assert action == "payout_account.status_changed"
    assert result == "succeeded"
    assert isinstance(metadata, dict)
    assert metadata["source"] == "provider"
    assert metadata["charges_enabled"] == {"from": False, "to": True}


def test_identical_status_refresh_adds_no_audit_noise(seller: UUID) -> None:
    # account.updated arrives often with nothing changed; auditing every
    # delivery would bury the one change that matters.
    assert RUNTIME_URL and ADMIN_URL
    request_id = uuid4()
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, SYNC, None)
        synced = _sync(connection, ACCOUNT, request_id)
        connection.commit()
    assert synced is not None
    with psycopg.connect(ADMIN_URL) as connection:
        audit = connection.execute(
            "select count(*) from public.audit_event where request_id = %s",
            (request_id,),
        ).fetchone()
    assert audit == (0,)


def test_unknown_account_returns_null_and_writes_nothing(seller: UUID) -> None:
    # The provider retries every non-2xx for days; an account this platform
    # never bound can never become bindable by retrying, so the function must
    # come back empty-handed instead of raising.
    assert RUNTIME_URL and ADMIN_URL
    request_id = uuid4()
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, SYNC, None)
        assert _sync(connection, UNKNOWN_ACCOUNT, request_id) is None
        connection.commit()
    with psycopg.connect(ADMIN_URL) as connection:
        audit = connection.execute(
            "select count(*) from public.audit_event where request_id = %s",
            (request_id,),
        ).fetchone()
    assert audit == (0,)


def test_sync_refuses_the_user_facing_capability(seller: UUID) -> None:
    # The manage capability belongs to the tenant-gated path. Letting it reach
    # the tenant-LESS sync would let any seller owner rewrite the status cache
    # of ANY account by id.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, MANAGE, seller)
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
            connection.execute(
                "select app_private.sync_payout_account_status('stripe',%s,true,true,true,null,%s)",
                (ACCOUNT, uuid4()),
            )
        assert denied.value.sqlstate == INSUFFICIENT_PRIVILEGE


def test_sync_refuses_a_tenant_scoped_context(seller: UUID) -> None:
    # Even with the right capability string, a context that carries a seller is
    # a user-shaped context and must not drive provider-shaped writes.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, SYNC, seller)
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
            connection.execute(
                "select app_private.sync_payout_account_status('stripe',%s,true,true,true,null,%s)",
                (ACCOUNT, uuid4()),
            )
        assert denied.value.sqlstate == INSUFFICIENT_PRIVILEGE


def test_sync_requires_a_request_id(seller: UUID) -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, SYNC, None)
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
            connection.execute(
                "select app_private.sync_payout_account_status('stripe',%s,"
                "true,true,true,null,null)",
                (ACCOUNT,),
            )
        assert denied.value.sqlstate == INSUFFICIENT_PRIVILEGE


def test_function_posture_is_security_definer_with_pinned_search_path(
    seller: UUID,
) -> None:
    # Catches the pg_temp shadowing class (#158) being reintroduced.
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select prosecdef, proconfig from pg_proc where oid = "
            "'app_private.sync_payout_account_status(text,text,boolean,boolean,"
            "boolean,text,uuid)'::regprocedure"
        ).fetchone()
    assert row is not None
    prosecdef, proconfig = row
    assert prosecdef is True
    search_path = next(entry for entry in proconfig if entry.startswith("search_path="))
    assert search_path.endswith("pg_temp")
