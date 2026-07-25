"""Migration 0021: the seller-to-payout-account binding.

Each test states the failure mode it catches, because this table decides WHERE A
SALE'S MONEY GOES. A permissive gate here does not corrupt data — it redirects
someone else's revenue.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
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
SELLER_C_SLUG = "payout-test-c"
ACCOUNT_A = "acct_test_payout_a"
ACCOUNT_B = "acct_test_payout_b"
INSUFFICIENT_PRIVILEGE = "42501"
RAISE_EXCEPTION = "55000"

READ = "commerce.payout.read"
MANAGE = "commerce.payout.manage"


@pytest.fixture(scope="module")
def sellers() -> dict[str, UUID]:
    # Self-contained tenants: two sellers are required because every meaningful
    # failure mode of this table is cross-tenant.
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        # Apaga por SELLER, nao por conta: limpar so ACCOUNT_A/ACCOUNT_B deixava
        # binding de rerun anterior vivo (ex. a conta da corrida), e o teste de
        # PRIMEIRA gravacao passava exercitando update de linha existente.
        connection.execute(
            "delete from public.seller_payout_account where seller_organization_id in "
            "(select id from seller_organization where slug in (%s,%s,%s))",
            (SELLER_A_SLUG, SELLER_B_SLUG, SELLER_C_SLUG),
        )
        created: dict[str, UUID] = {}
        for slug in (SELLER_A_SLUG, SELLER_B_SLUG, SELLER_C_SLUG):
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
    # Catches a runtime-facing grant or policy being added by accident. The
    # runtime must only ever see this table through the capability-gated
    # functions; a direct select would expose every seller's payout account.
    #
    # Asserts the exact SQLSTATE and the catalog, not a bare psycopg.Error: a
    # bare Error would also pass on a typo or a missing table, proving nothing.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.Error) as raised:
            connection.execute("select 1 from public.seller_payout_account")
        assert raised.value.sqlstate == INSUFFICIENT_PRIVILEGE
        connection.rollback()
        privileges = connection.execute(
            "select has_table_privilege('appsell_runtime','public.seller_payout_account',p) "
            "from unnest(array['select','insert','update','delete']) p"
        ).fetchall()
    assert [row[0] for row in privileges] == [False, False, False, False]


def test_upsert_without_capability_is_denied(sellers: dict[str, UUID]) -> None:
    # Catches the capability gate being absent: any authenticated request could
    # then bind or rebind a payout account.
    assert RUNTIME_URL
    seller = sellers[SELLER_A_SLUG]
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, READ, seller)  # read capability, not manage
        with pytest.raises(psycopg.Error) as raised:
            _upsert(connection, seller, ACCOUNT_A)
        assert raised.value.sqlstate == INSUFFICIENT_PRIVILEGE


def test_upsert_for_another_seller_is_denied(sellers: dict[str, UUID]) -> None:
    # THE cross-tenant money bug: seller A holding a valid manage capability in
    # its own context must not be able to write a binding for seller B.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, MANAGE, sellers[SELLER_A_SLUG])
        with pytest.raises(psycopg.Error) as raised:
            _upsert(connection, sellers[SELLER_B_SLUG], ACCOUNT_B)
        assert raised.value.sqlstate == INSUFFICIENT_PRIVILEGE


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
        with pytest.raises(psycopg.Error) as raised:
            _upsert(connection, seller, "acct_test_payout_hijack")
        assert raised.value.sqlstate == RAISE_EXCEPTION


def test_resolve_for_another_seller_is_denied(sellers: dict[str, UUID]) -> None:
    # Cross-tenant read. The connected-account id alone cannot move money, but
    # it identifies another producer's payment account.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, READ, sellers[SELLER_A_SLUG])
        with pytest.raises(psycopg.Error) as raised:
            connection.execute(
                "select 1 from app_private.resolve_seller_payout_account(%s,'stripe')",
                (sellers[SELLER_B_SLUG],),
            )
        assert raised.value.sqlstate == INSUFFICIENT_PRIVILEGE


def test_one_provider_account_cannot_serve_two_sellers(sellers: dict[str, UUID]) -> None:
    # The DB-level tenant guarantee: even with a correct capability in its own
    # context, seller B must not be able to claim the account already bound to
    # seller A. Direction matters and the first version of this comment had it
    # backwards: B claiming A's account would send B's OWN sales into A's
    # account, and would also deny B a usable binding.
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


def test_security_definer_functions_are_hardened(sellers: dict[str, UUID]) -> None:
    # Catches the RLS/search_path posture silently regressing. The previous test
    # only proves the runtime cannot SELECT, which stays true even with RLS off
    # (the runtime has no grant), so it does not prove RLS at all.
    #
    # pg_temp explicitly LAST matters: when it is not listed, PostgreSQL searches
    # the temporary schema FIRST for relations, so a temp table named like ours
    # would shadow it inside a security definer body.
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        rls = connection.execute(
            "select relrowsecurity, relforcerowsecurity from pg_catalog.pg_class "
            "where oid = 'public.seller_payout_account'::regclass"
        ).fetchone()
        policies = connection.execute(
            "select polname, polroles::regrole[]::text[] from pg_catalog.pg_policy "
            "where polrelid = 'public.seller_payout_account'::regclass"
        ).fetchall()
        configs = connection.execute(
            "select proname, proconfig, prosecdef from pg_catalog.pg_proc "
            "where pronamespace = 'app_private'::regnamespace "
            "and proname in ('resolve_seller_payout_account','upsert_seller_payout_account') "
            "order by proname"
        ).fetchall()
    assert rls == (True, True)
    assert [row[0] for row in policies] == ["seller_payout_account_owner_all"]
    assert policies[0][1] == ["appsell_owner"]
    assert len(configs) == 2
    for name, proconfig, secdef in configs:
        assert secdef is True, name
        joined = " ".join(proconfig or [])
        assert "pg_temp" in joined, name
        assert joined.rstrip().endswith("pg_temp"), name


def test_a_temp_table_cannot_shadow_the_payout_table(sellers: dict[str, UUID]) -> None:
    # Direct proof for the pg_temp shadowing class: if the function body reached
    # an unqualified name with the temp schema searched first, a forged temp row
    # would come back and the next direct charge could be pointed elsewhere.
    assert RUNTIME_URL
    seller = sellers[SELLER_A_SLUG]
    forged = "acct_test_payout_forged"
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, MANAGE, seller)
        _upsert(connection, seller, ACCOUNT_A)
        connection.commit()
        # No `except` here on purpose. A broad one would swallow a column, syntax
        # or privilege error and then "pass" on the untouched real table — the
        # exact false green this test exists to prevent. TEMPORARY is currently
        # granted to the runtime (001_roles.sh revokes only CONNECT), so both
        # statements MUST succeed; when #158 revokes it, this becomes a separate
        # assertion that the create is refused with 42501.
        connection.execute(
            "create temporary table seller_payout_account "
            "(id uuid, seller_organization_id uuid, provider text, "
            "provider_account_id text, charges_enabled boolean, "
            "payouts_enabled boolean, details_submitted boolean, "
            "disabled_reason text, status_synced_at timestamptz)"
        )
        connection.execute(
            "insert into seller_payout_account values "
            "(gen_random_uuid(),%s,'stripe',%s,true,true,true,null,now())",
            (seller, forged),
        )
        # Proves the shadow is really in front: unqualified now hits the temp copy.
        shadowed = connection.execute(
            "select provider_account_id from seller_payout_account"
        ).fetchone()
        assert shadowed is not None and shadowed[0] == forged
        _context(connection, READ, seller)
        row = connection.execute(
            "select provider_account_id from "
            "app_private.resolve_seller_payout_account(%s,'stripe')",
            (seller,),
        ).fetchone()
    assert row is not None
    assert row[0] == ACCOUNT_A, "the security definer body read a shadowing temp table"


def test_concurrent_first_bind_is_idempotent(sellers: dict[str, UUID]) -> None:
    # Catches the FIRST-write race that `for update` cannot cover: with no row
    # there is nothing to lock, so two simultaneous binds of one seller (a
    # producer retry racing the account.updated webhook) both reach the insert
    # and one takes a 23505 on an operation that must be idempotent.
    #
    # The barrier is an OBSERVED database lock, not a timer. Two earlier versions
    # were not deterministic: firing two threads and hoping they overlap passes on
    # the unlocked implementation whenever the second starts after the first
    # commits, and signalling an Event before calling upsert has the same hole —
    # the rival can set the flag, lose the CPU, and only reach the upsert after
    # the commit. Here the main connection polls pg_blocking_pids through a THIRD
    # (admin) connection until it sees itself blocking the rival, which proves the
    # rival is parked inside the critical section right now. Only then does it
    # commit. Remove the advisory lock and the rival blocks on the UNIQUE index
    # instead — still observably blocked, and it surfaces 23505 after the commit,
    # which is exactly the regression this asserts against.
    assert RUNTIME_URL
    assert ADMIN_URL
    seller = sellers[SELLER_C_SLUG]
    account = "acct_test_payout_race"
    outcome: dict[str, object] = {}

    def contend() -> None:
        assert RUNTIME_URL
        with psycopg.connect(RUNTIME_URL) as rival:
            _context(rival, MANAGE, seller)
            pid = rival.execute("select pg_backend_pid()").fetchone()
            assert pid is not None
            outcome["pid"] = int(pid[0])
            try:
                outcome["id"] = _upsert(rival, seller, account, charges=True)
                rival.commit()
            except psycopg.Error as error:
                outcome["sqlstate"] = error.sqlstate

    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, MANAGE, seller)
        own_pid_row = connection.execute("select pg_backend_pid()").fetchone()
        assert own_pid_row is not None
        own_pid = int(own_pid_row[0])
        first = _upsert(connection, seller, account)

        thread = threading.Thread(target=contend)
        thread.start()
        deadline = time.monotonic() + 30
        blocked = False
        with psycopg.connect(ADMIN_URL) as observer:
            while time.monotonic() < deadline:
                rival_pid = outcome.get("pid")
                if isinstance(rival_pid, int):
                    blockers = observer.execute(
                        "select pg_blocking_pids(%s)", (rival_pid,)
                    ).fetchone()
                    observer.rollback()
                    if blockers is not None and own_pid in list(blockers[0]):
                        blocked = True
                        break
                time.sleep(0.05)
        assert blocked, "o rival nunca ficou bloqueado por esta transacao"
        connection.commit()
        thread.join(timeout=30)
    assert not thread.is_alive(), "o rival nunca destravou"

    assert "sqlstate" not in outcome, f"segundo bind falhou com {outcome.get('sqlstate')}"
    assert outcome["id"] == first, "o segundo bind criou um vinculo diferente"

    with psycopg.connect(ADMIN_URL) as connection:
        count = connection.execute(
            "select count(*) from public.seller_payout_account where seller_organization_id=%s",
            (seller,),
        ).fetchone()
    assert count is not None
    assert count[0] == 1
