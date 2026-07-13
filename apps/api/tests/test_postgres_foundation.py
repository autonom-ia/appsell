from __future__ import annotations

import hashlib
import hmac
import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
import pytest
from psycopg import sql

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")
BOOTSTRAP_KEY_HEX = os.environ.get("APPSELL_TEST_BOOTSTRAP_KEY_HEX")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX, BOOTSTRAP_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

USER_A = UUID("10000000-0000-4000-8000-000000000001")
USER_B = UUID("10000000-0000-4000-8000-000000000002")
SELLER_A = UUID("20000000-0000-4000-8000-000000000001")
SELLER_B = UUID("20000000-0000-4000-8000-000000000002")
BOOTSTRAP_ISSUER = "https://identity.dev.autonomia.site"
BOOTSTRAP_AUDIENCE = "appsell-web-dev"
BOOTSTRAP_CLIENT_ID = "appsell-web-dev"
FOUNDATION_DATABASE_WORKERS = 12


def derived_identity_user_id(issuer: str, subject: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"{issuer}\0{subject}")


def sign_context(
    identity_user_id: UUID,
    seller_organization_id: UUID | None,
    capability: str,
    issued_at: int,
    expires_at: int,
) -> str:
    seller = str(seller_organization_id) if seller_organization_id else ""
    payload = f"{identity_user_id}:{seller}:{capability}:{issued_at}:{expires_at}"
    return hmac.new(
        bytes.fromhex(CONTEXT_KEY_HEX or ""), payload.encode(), hashlib.sha256
    ).hexdigest()


def apply_context(
    connection: psycopg.Connection[tuple[object, ...]],
    identity_user_id: UUID,
    seller_organization_id: UUID | None,
    capability: str = "seller.read",
) -> None:
    issued_at = int(time.time())
    expires_at = issued_at + 60
    signature = sign_context(
        identity_user_id, seller_organization_id, capability, issued_at, expires_at
    )
    connection.execute(
        "select app_private.apply_request_context(%s, %s, %s, %s, %s, %s)",
        (
            identity_user_id,
            seller_organization_id,
            capability,
            issued_at,
            expires_at,
            signature,
        ),
    )


def sign_bootstrap_attestation(
    identity_user_id: UUID,
    identity_sub: str,
    email: str,
    email_verified: bool,
    assurance: str,
    issuer: str,
    audience: str,
    client_id: str,
    request_id: UUID,
    issued_at: int,
    expires_at: int,
) -> str:
    def field_hash(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    payload = ":".join(
        (
            "appsell.bootstrap.v1",
            str(identity_user_id),
            field_hash(identity_sub),
            field_hash(email.strip().lower()),
            str(email_verified).lower(),
            assurance,
            field_hash(issuer),
            field_hash(audience),
            field_hash(client_id),
            str(request_id),
            str(issued_at),
            str(expires_at),
        )
    )
    return hmac.new(
        bytes.fromhex(BOOTSTRAP_KEY_HEX or ""), payload.encode(), hashlib.sha256
    ).hexdigest()


@pytest.fixture(scope="module", autouse=True)
def seed_foundation() -> None:
    assert ADMIN_URL and CONTEXT_KEY_HEX
    with psycopg.connect(ADMIN_URL, autocommit=True) as connection:
        connection.execute(
            """
            truncate audit_event, seller_membership, seller_organization,
              superadmin_bootstrap, platform_grant, identity_user cascade
            """
        )
        connection.execute("truncate app_private.context_signing_key")
        connection.execute(
            "insert into app_private.context_signing_key (secret) values (%s)",
            (bytes.fromhex(CONTEXT_KEY_HEX),),
        )
        connection.execute(
            """
            insert into identity_user (
              id, identity_issuer, identity_sub, email_normalized, email_verified
            ) values (%s,%s,%s,%s,true),(%s,%s,%s,%s,true)
            """,
            (
                USER_A,
                BOOTSTRAP_ISSUER,
                "identity-a",
                "a@example.test",
                USER_B,
                BOOTSTRAP_ISSUER,
                "identity-b",
                "b@example.test",
            ),
        )
        connection.execute(
            """
            insert into seller_organization (id, slug, display_name, status)
            values (%s,'seller-a','Seller A','active'),
                   (%s,'seller-b','Seller B','active')
            """,
            (SELLER_A, SELLER_B),
        )
        connection.execute(
            """
            insert into seller_membership (
              seller_organization_id, identity_user_id, role, status
            ) values (%s,%s,'seller_owner','active'),
                     (%s,%s,'seller_owner','active')
            """,
            (SELLER_A, USER_A, SELLER_B, USER_B),
        )


def test_schema_readiness_requires_configured_signing_key() -> None:
    assert ADMIN_URL and RUNTIME_URL and CONTEXT_KEY_HEX
    with psycopg.connect(ADMIN_URL, autocommit=True) as connection:
        connection.execute("truncate app_private.context_signing_key")
    with psycopg.connect(RUNTIME_URL) as connection:
        assert connection.execute("select app_private.schema_ready()").fetchone() == (False,)
    with psycopg.connect(ADMIN_URL, autocommit=True) as connection:
        connection.execute(
            "insert into app_private.context_signing_key (secret) values (%s)",
            (bytes.fromhex(CONTEXT_KEY_HEX),),
        )


def test_database_roles_are_non_privileged() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        rows = connection.execute(
            """
            select rolname, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls,
                   rolcanlogin, rolinherit
            from pg_roles where rolname = any(%s)
            """,
            (["appsell_owner", "appsell_migrator", "appsell_runtime"],),
        ).fetchall()
    assert {row[0] for row in rows} == {"appsell_owner", "appsell_migrator", "appsell_runtime"}
    assert all(row[1:5] == (False, False, False, False) for row in rows)
    login_and_inherit = {row[0]: row[5:] for row in rows}
    assert login_and_inherit == {
        "appsell_owner": (False, False),
        "appsell_migrator": (True, True),
        "appsell_runtime": (True, False),
    }

    with psycopg.connect(ADMIN_URL) as connection:
        public_connect = connection.execute(
            """
            select count(*)
            from pg_database database
            cross join lateral aclexplode(
              coalesce(database.datacl, acldefault('d', database.datdba))
            ) acl
            where database.datname = current_database()
              and acl.grantee = 0
              and acl.privilege_type = 'CONNECT'
            """
        ).fetchone()
    assert public_connect == (0,)

    with psycopg.connect(ADMIN_URL) as connection:
        grants = connection.execute(
            """
            select
              pg_has_role('appsell_runtime', 'appsell_owner', 'MEMBER'),
              has_schema_privilege('appsell_runtime', 'app_private', 'USAGE'),
              has_schema_privilege('appsell_runtime', 'app_private', 'CREATE'),
              has_table_privilege(
                'appsell_runtime', 'app_private.context_signing_key', 'SELECT'
              ),
              has_table_privilege(
                'appsell_runtime', 'app_private.bootstrap_trust_policy', 'SELECT'
              ),
              has_function_privilege(
                'appsell_runtime', 'app_private.schema_ready()', 'EXECUTE'
              )
            """
        ).fetchone()
    assert grants == (False, True, False, False, False, True)

    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("set role appsell_owner")


def test_runtime_owns_no_foundation_table_and_rls_is_forced() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        rows = connection.execute(
            """
            select c.relname, owner.rolname, c.relrowsecurity, c.relforcerowsecurity
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            join pg_roles owner on owner.oid = c.relowner
            where n.nspname = 'public' and c.relkind = 'r'
              and c.relname in (
                'identity_user', 'platform_grant', 'seller_organization',
                'seller_membership', 'audit_event', 'superadmin_bootstrap'
              )
            """
        ).fetchall()
        private_owners = connection.execute(
            """
            select c.relname, owner.rolname
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            join pg_roles owner on owner.oid = c.relowner
            where n.nspname = 'app_private' and c.relkind = 'r'
              and c.relname in ('context_signing_key', 'bootstrap_trust_policy')
            order by c.relname
            """
        ).fetchall()
    assert len(rows) == 6
    assert all(row[1] == "appsell_owner" for row in rows)
    rls_rows = [row for row in rows if row[0] != "superadmin_bootstrap"]
    assert all(row[2] is True and row[3] is True for row in rls_rows)
    assert private_owners == [
        ("bootstrap_trust_policy", "appsell_owner"),
        ("context_signing_key", "appsell_owner"),
    ]


def test_context_is_signed_and_seller_rls_fails_closed() -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        assert connection.execute("select count(*) from seller_organization").fetchone() == (0,)
        apply_context(connection, USER_A, SELLER_A)
        assert connection.execute("select slug from seller_organization").fetchall() == [
            ("seller-a",)
        ]
        assert connection.execute(
            "select seller_organization_id from seller_membership"
        ).fetchall() == [(SELLER_A,)]


def test_cross_tenant_context_does_not_turn_membership_into_access() -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_B)
        assert connection.execute("select slug from seller_organization").fetchall() == []
        assert connection.execute(
            "select seller_organization_id from seller_membership"
        ).fetchall() == [(SELLER_A,)]


def test_invalid_signature_is_rejected() -> None:
    assert RUNTIME_URL
    now = int(time.time())
    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.apply_request_context(%s,%s,%s,%s,%s,%s)",
                (USER_A, SELLER_A, "seller.read", now, now + 60, "0" * 64),
            )


def test_expired_and_future_contexts_are_rejected() -> None:
    assert RUNTIME_URL
    now = int(time.time())
    cases = ((now - 120, now - 60), (now + 30, now + 60), (now + 5, now + 4))
    with psycopg.connect(RUNTIME_URL) as connection:
        for issued_at, expires_at in cases:
            signature = sign_context(USER_A, SELLER_A, "seller.read", issued_at, expires_at)
            with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
                connection.execute(
                    "select app_private.apply_request_context(%s,%s,%s,%s,%s,%s)",
                    (USER_A, SELLER_A, "seller.read", issued_at, expires_at, signature),
                )
            connection.rollback()


def test_direct_session_variable_forgery_fails_closed() -> None:
    assert RUNTIME_URL
    forged_payload = f"{USER_A}:{SELLER_A}:seller.read:{int(time.time())}:{int(time.time()) + 60}"
    with psycopg.connect(RUNTIME_URL) as connection:
        connection.execute(
            "select set_config('appsell.context_payload', %s, true)", (forged_payload,)
        )
        connection.execute("select set_config('appsell.context_signature', %s, true)", ("0" * 64,))
        assert connection.execute("select count(*) from seller_organization").fetchone() == (0,)


def test_direct_signed_context_with_expiry_before_issue_fails_closed() -> None:
    assert RUNTIME_URL
    issued_at = int(time.time()) + 5
    expires_at = issued_at - 1
    payload = f"{USER_A}:{SELLER_A}:seller.read:{issued_at}:{expires_at}"
    signature = sign_context(USER_A, SELLER_A, "seller.read", issued_at, expires_at)
    with psycopg.connect(RUNTIME_URL) as connection:
        connection.execute("select set_config('appsell.context_payload', %s, true)", (payload,))
        connection.execute("select set_config('appsell.context_signature', %s, true)", (signature,))
        assert connection.execute("select count(*) from seller_organization").fetchone() == (0,)


def test_transaction_context_does_not_leak_after_commit() -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_A)
        assert connection.execute("select count(*) from seller_organization").fetchone() == (1,)
        connection.commit()
        assert connection.execute("select count(*) from seller_organization").fetchone() == (0,)


def test_transaction_context_does_not_leak_after_rollback() -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_A)
        assert connection.execute("select count(*) from seller_organization").fetchone() == (1,)
        connection.rollback()
        assert connection.execute("select count(*) from seller_organization").fetchone() == (0,)


def test_audit_is_tenant_scoped_and_append_only() -> None:
    assert RUNTIME_URL
    event_id = UUID("30000000-0000-4000-8000-000000000001")
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_A, "audit.write")
        connection.execute(
            """
            insert into audit_event (
              id, seller_organization_id, actor_identity_user_id, action,
              target_type, result, request_id
            ) values (%s,%s,%s,'seller.previewed','seller','succeeded',gen_random_uuid())
            """,
            (event_id, SELLER_A, USER_A),
        )
        connection.commit()

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_A, "audit.read")
        assert connection.execute(
            "select id from audit_event where id = %s", (event_id,)
        ).fetchall() == [(event_id,)]

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_B, SELLER_B, "audit.read")
        assert (
            connection.execute("select id from audit_event where id = %s", (event_id,)).fetchall()
            == []
        )

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_A, "seller.read")
        assert (
            connection.execute("select id from audit_event where id = %s", (event_id,)).fetchall()
            == []
        )

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_B, "audit.write")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                """
                insert into audit_event (
                  seller_organization_id, actor_identity_user_id, action,
                  target_type, result, request_id
                ) values (%s,%s,'seller.previewed','seller','denied',gen_random_uuid())
                """,
                (SELLER_B, USER_A),
            )

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_A, "seller.read")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                """
                insert into audit_event (
                  seller_organization_id, actor_identity_user_id, action,
                  target_type, result, request_id
                ) values (%s,%s,'seller.previewed','seller','denied',gen_random_uuid())
                """,
                (SELLER_A, USER_A),
            )

    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                sql.SQL("update audit_event set result = 'failed' where id = %s"), (event_id,)
            )


def test_bootstrap_subject_must_match_identity() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                """
                insert into superadmin_bootstrap (
                  identity_user_id, identity_sub, assurance, request_id
                ) values (%s, 'identity-b', 'mfa', gen_random_uuid())
                """,
                (USER_A,),
            )


def bootstrap_candidate(
    identity_user_id: UUID,
    identity_sub: str,
    email: str = "comercial@autonomia.site",
    email_verified: bool = True,
    assurance: str = "mfa",
    capability: str = "platform.bootstrap",
    issuer: str = BOOTSTRAP_ISSUER,
    audience: str = BOOTSTRAP_AUDIENCE,
    client_id: str = BOOTSTRAP_CLIENT_ID,
    signature_override: str | None = None,
) -> UUID:
    assert RUNTIME_URL
    request_id = uuid4()
    issued_at = int(time.time())
    expires_at = issued_at + 60
    signature = signature_override or sign_bootstrap_attestation(
        identity_user_id,
        identity_sub,
        email,
        email_verified,
        assurance,
        issuer,
        audience,
        client_id,
        request_id,
        issued_at,
        expires_at,
    )
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, identity_user_id, None, capability)
        row = connection.execute(
            """
            select app_private.consume_superadmin_bootstrap(
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                identity_user_id,
                identity_sub,
                email,
                email_verified,
                assurance,
                issuer,
                audience,
                client_id,
                request_id,
                "a" * 64,
                issued_at,
                expires_at,
                signature,
            ),
        ).fetchone()
        assert row is not None
        result = row[0]
        assert isinstance(result, UUID)
        return result


@pytest.mark.parametrize(
    ("email", "email_verified", "assurance", "capability"),
    [
        ("attacker@example.test", True, "mfa", "platform.bootstrap"),
        ("comercial@autonomia.site", False, "mfa", "platform.bootstrap"),
        ("comercial@autonomia.site", True, "password", "platform.bootstrap"),
        ("comercial@autonomia.site", True, "mfa", "seller.read"),
    ],
)
def test_bootstrap_rejects_untrusted_claims(
    email: str, email_verified: bool, assurance: str, capability: str
) -> None:
    identity_sub = f"rejected-{uuid4()}"
    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        bootstrap_candidate(
            derived_identity_user_id(BOOTSTRAP_ISSUER, identity_sub),
            identity_sub,
            email,
            email_verified,
            assurance,
            capability,
        )


@pytest.mark.parametrize(
    "identity_sub", [" leading-sub", "trailing-sub ", "bad\nsub", "bad\u0085sub", "ab"]
)
def test_bootstrap_rejects_noncanonical_subject(identity_sub: str) -> None:
    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        bootstrap_candidate(derived_identity_user_id(BOOTSTRAP_ISSUER, identity_sub), identity_sub)


def test_identity_sync_treats_nbsp_as_exact_data() -> None:
    assert RUNTIME_URL
    identity_sub = f"\u00a0subject-{uuid4()}"
    identity_user_id = derived_identity_user_id(BOOTSTRAP_ISSUER, identity_sub)
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, identity_user_id, None, "identity.sync")
        row = connection.execute(
            "select app_private.sync_identity(%s, %s, %s, %s, true)",
            (identity_user_id, BOOTSTRAP_ISSUER, identity_sub, "nbsp@example.test"),
        ).fetchone()
        assert row == (identity_user_id,)


def test_bootstrap_attestation_rejects_claim_substitution() -> None:
    original_sub = f"original-{uuid4()}"
    identity_user_id = uuid5(NAMESPACE_URL, f"{BOOTSTRAP_ISSUER}\0{original_sub}")
    request_id = uuid4()
    issued_at = int(time.time())
    signature = sign_bootstrap_attestation(
        identity_user_id,
        original_sub,
        "comercial@autonomia.site",
        True,
        "mfa",
        BOOTSTRAP_ISSUER,
        BOOTSTRAP_AUDIENCE,
        BOOTSTRAP_CLIENT_ID,
        request_id,
        issued_at,
        issued_at + 60,
    )
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, identity_user_id, None, "platform.bootstrap")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                """
                select app_private.consume_superadmin_bootstrap(
                  %s, %s, %s, true, 'mfa', %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    identity_user_id,
                    f"substituted-{uuid4()}",
                    "comercial@autonomia.site",
                    BOOTSTRAP_ISSUER,
                    BOOTSTRAP_AUDIENCE,
                    BOOTSTRAP_CLIENT_ID,
                    request_id,
                    "a" * 64,
                    issued_at,
                    issued_at + 60,
                    signature,
                ),
            )


def test_bootstrap_attestation_rejects_null_timestamps() -> None:
    identity_sub = f"null-time-{uuid4()}"
    identity_user_id = uuid5(NAMESPACE_URL, f"{BOOTSTRAP_ISSUER}\0{identity_sub}")
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, identity_user_id, None, "platform.bootstrap")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                """
                select app_private.consume_superadmin_bootstrap(
                  %s, %s, %s, true, 'mfa', %s, %s, %s, %s, %s, null, null, %s
                )
                """,
                (
                    identity_user_id,
                    identity_sub,
                    "comercial@autonomia.site",
                    BOOTSTRAP_ISSUER,
                    BOOTSTRAP_AUDIENCE,
                    BOOTSTRAP_CLIENT_ID,
                    uuid4(),
                    "a" * 64,
                    "0" * 64,
                ),
            )


@pytest.mark.parametrize(
    ("issuer", "audience", "client_id"),
    [
        ("https://evil.example.test", BOOTSTRAP_AUDIENCE, BOOTSTRAP_CLIENT_ID),
        (BOOTSTRAP_ISSUER, "other-audience", BOOTSTRAP_CLIENT_ID),
        (BOOTSTRAP_ISSUER, BOOTSTRAP_AUDIENCE, "other-client"),
    ],
)
def test_bootstrap_rejects_wrong_token_trust_policy(
    issuer: str, audience: str, client_id: str
) -> None:
    identity_sub = f"wrong-trust-{uuid4()}"
    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        bootstrap_candidate(
            derived_identity_user_id(issuer, identity_sub),
            identity_sub,
            issuer=issuer,
            audience=audience,
            client_id=client_id,
        )


def test_bootstrap_rejects_invalid_or_expired_attestation() -> None:
    invalid_sub = f"invalid-signature-{uuid4()}"
    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        bootstrap_candidate(
            derived_identity_user_id(BOOTSTRAP_ISSUER, invalid_sub),
            invalid_sub,
            signature_override="0" * 64,
        )

    assert RUNTIME_URL
    now = int(time.time())
    for issued_at, expires_at in ((now - 180, now - 120), (now + 5, now + 4)):
        identity_sub = f"invalid-time-{uuid4()}"
        identity_user_id = uuid5(NAMESPACE_URL, f"{BOOTSTRAP_ISSUER}\0{identity_sub}")
        request_id = uuid4()
        signature = sign_bootstrap_attestation(
            identity_user_id,
            identity_sub,
            "comercial@autonomia.site",
            True,
            "mfa",
            BOOTSTRAP_ISSUER,
            BOOTSTRAP_AUDIENCE,
            BOOTSTRAP_CLIENT_ID,
            request_id,
            issued_at,
            expires_at,
        )
        with psycopg.connect(RUNTIME_URL) as connection:
            apply_context(connection, identity_user_id, None, "platform.bootstrap")
            with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
                connection.execute(
                    """
                    select app_private.consume_superadmin_bootstrap(
                      %s, %s, %s, true, 'mfa', %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        identity_user_id,
                        identity_sub,
                        "comercial@autonomia.site",
                        BOOTSTRAP_ISSUER,
                        BOOTSTRAP_AUDIENCE,
                        BOOTSTRAP_CLIENT_ID,
                        request_id,
                        "a" * 64,
                        issued_at,
                        expires_at,
                        signature,
                    ),
                )


def test_bootstrap_disable_is_linearizable_against_concurrent_consume() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as admin_connection:
        admin_connection.execute("set local role appsell_owner")
        admin_connection.execute(
            "select enabled from app_private.bootstrap_trust_policy where singleton for update"
        )
        admin_connection.execute(
            "update app_private.bootstrap_trust_policy set enabled = false where singleton"
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            disabled_sub = f"disabled-race-{uuid4()}"
            future = executor.submit(
                bootstrap_candidate,
                derived_identity_user_id(BOOTSTRAP_ISSUER, disabled_sub),
                disabled_sub,
            )
            time.sleep(0.1)
            assert not future.done()
            admin_connection.commit()
            with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
                future.result(timeout=5)

    with psycopg.connect(ADMIN_URL, autocommit=True) as connection:
        state = connection.execute(
            """
            select
              (select count(*) from superadmin_bootstrap),
              (select count(*) from platform_grant
                where role = 'platform_superadmin' and status = 'active')
            """
        ).fetchone()
        assert state == (0, 0)
        connection.execute(
            "update app_private.bootstrap_trust_policy set enabled = true where singleton"
        )


def test_bootstrap_has_exactly_one_concurrent_winner_and_grant_survives_email_change() -> None:
    subjects = [f"bootstrap-{uuid4()}" for _ in range(20)]
    candidates = [
        (derived_identity_user_id(BOOTSTRAP_ISSUER, subject), subject) for subject in subjects
    ]
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL, autocommit=True) as connection:
        connection.cursor().executemany(
            """
            insert into identity_user (
              id, identity_issuer, identity_sub, email_normalized, email_verified
            ) values (%s, %s, %s, %s, true)
            """,
            [
                (
                    identity_user_id,
                    BOOTSTRAP_ISSUER,
                    identity_sub,
                    f"candidate-{index}@example.test",
                )
                for index, (identity_user_id, identity_sub) in enumerate(candidates)
            ],
        )

    def attempt(candidate: tuple[UUID, str]) -> UUID | None:
        try:
            return bootstrap_candidate(*candidate)
        except (
            psycopg.errors.UniqueViolation,
            psycopg.errors.InvalidAuthorizationSpecification,
        ):
            return None

    with ThreadPoolExecutor(max_workers=FOUNDATION_DATABASE_WORKERS) as executor:
        results = list(executor.map(attempt, candidates))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    winner = winners[0]

    with psycopg.connect(ADMIN_URL, autocommit=True) as connection:
        state = connection.execute(
            """
            select
              (select count(*) from superadmin_bootstrap),
              (select count(*) from platform_grant
                where role = 'platform_superadmin' and status = 'active'),
              (select count(*) from audit_event
                where action = 'platform.superadmin_bootstrapped')
            """
        ).fetchone()
        assert state == (1, 1, 1)
        connection.execute(
            "update identity_user set email_normalized = 'changed@example.test' where id = %s",
            (winner,),
        )
        grant = connection.execute(
            """
            select role, status from platform_grant
            where identity_user_id = %s
            """,
            (winner,),
        ).fetchone()
        assert grant == ("platform_superadmin", "active")

    winner_subject = dict(candidates)[winner]
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, winner, None, "identity.sync")
        synced = connection.execute(
            "select app_private.sync_identity(%s,%s,%s,%s,true)",
            (
                winner,
                BOOTSTRAP_ISSUER,
                winner_subject,
                "comercial@autonomia.site",
            ),
        ).fetchone()
        assert synced == (winner,)
        connection.commit()

        apply_context(connection, winner, None, "platform.grants.read")
        assert connection.execute(
            "select role, status from platform_grant where identity_user_id = %s",
            (winner,),
        ).fetchone() == ("platform_superadmin", "active")

    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        bootstrap_candidate(uuid4(), f"replay-{uuid4()}")
