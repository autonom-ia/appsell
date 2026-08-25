from __future__ import annotations

import hashlib
import hmac
import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
import pytest

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

ISSUER_A = "https://identity-a.dev.autonomia.site"
ISSUER_B = "https://identity-b.dev.autonomia.site"


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
    capability: str,
) -> None:
    issued_at = int(time.time())
    expires_at = issued_at + 60
    connection.execute(
        "select app_private.apply_request_context(%s, %s, %s, %s, %s, %s)",
        (
            identity_user_id,
            seller_organization_id,
            capability,
            issued_at,
            expires_at,
            sign_context(
                identity_user_id,
                seller_organization_id,
                capability,
                issued_at,
                expires_at,
            ),
        ),
    )


def sync_identity(
    identity_user_id: UUID | None = None,
    issuer: str = ISSUER_A,
    subject: str | None = None,
    email: str | None = None,
    email_verified: bool = True,
) -> UUID:
    subject = subject or f"test-sub-{uuid4()}"
    identity_user_id = identity_user_id or uuid5(NAMESPACE_URL, f"{issuer}\0{subject}")
    email = email or f"test-{uuid4()}@example.test"
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, identity_user_id, None, "identity.sync")
        row = connection.execute(
            "select app_private.sync_identity(%s, %s, %s, %s, %s)",
            (identity_user_id, issuer, subject, email, email_verified),
        ).fetchone()
        assert row is not None and isinstance(row[0], UUID)
        connection.commit()
        return row[0]


def onboard(
    identity_user_id: UUID,
    slug: str,
    display_name: str,
    idempotency_key: UUID,
    body_sha256: str,
) -> UUID:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, identity_user_id, None, "seller.onboard")
        row = connection.execute(
            "select app_private.onboard_seller(%s, %s, %s, %s, %s, %s)",
            (
                identity_user_id,
                slug,
                display_name,
                idempotency_key,
                body_sha256,
                uuid4(),
            ),
        ).fetchone()
        assert row is not None and isinstance(row[0], UUID)
        connection.commit()
        return row[0]


def test_schema_ready_targets_head_and_runtime_has_only_function_access() -> None:
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        assert connection.execute("select app_private.schema_ready()").fetchone() == (True,)
    with psycopg.connect(ADMIN_URL) as connection:
        state = connection.execute(
            """
            select
              (select version_num from alembic_version),
              has_table_privilege(
                'appsell_runtime', 'app_private.bff_request_replay', 'INSERT'
              ),
              has_table_privilege(
                'appsell_runtime', 'app_private.seller_onboarding_request', 'INSERT'
              ),
              has_function_privilege(
                'appsell_runtime', 'app_private.consume_bff_request(uuid,bigint)', 'EXECUTE'
              ),
              has_function_privilege(
                'appsell_runtime',
                'app_private.sync_identity(uuid,text,text,text,boolean)', 'EXECUTE'
              ),
              has_function_privilege(
                'appsell_runtime',
                'app_private.onboard_seller(uuid,text,text,uuid,text,uuid)', 'EXECUTE'
              )
            """
        ).fetchone()
    assert state == ("0038_player_hides_materials", False, False, True, True, True)


def test_schema_ready_fails_when_cumulative_or_multi_asset_dependency_is_missing() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "alter table app_private.asset_upload rename to asset_upload_readiness_probe"
        )
        assert connection.execute("select app_private.schema_ready()").fetchone() == (False,)
        connection.rollback()

    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("drop function app_private.read_buyer_products(uuid)")
        assert connection.execute("select app_private.schema_ready()").fetchone() == (False,)
        connection.rollback()

    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("drop function app_private.read_learning_player_v2(uuid,uuid,uuid)")
        assert connection.execute("select app_private.schema_ready()").fetchone() == (False,)
        connection.rollback()

    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "alter table publication_asset disable trigger publication_lesson_asset_limit"
        )
        assert connection.execute("select app_private.schema_ready()").fetchone() == (False,)
        connection.rollback()


def test_identity_is_scoped_by_issuer_and_sync_creates_no_privilege() -> None:
    subject = f"shared-sub-{uuid4()}"
    first_id = uuid5(NAMESPACE_URL, f"{ISSUER_A}\0{subject}")
    other_id = uuid5(NAMESPACE_URL, f"{ISSUER_B}\0{subject}")
    first = sync_identity(first_id, ISSUER_A, subject, "first@example.test", True)
    repeated = sync_identity(first_id, ISSUER_A, subject, "updated@example.test", False)
    other_issuer = sync_identity(other_id, ISSUER_B, subject, "other@example.test", True)
    assert repeated == first
    assert other_issuer != first

    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        state = connection.execute(
            """
            select
              (select email_normalized from identity_user where id = %s),
              (select email_verified from identity_user where id = %s),
              (select count(*) from platform_grant where identity_user_id = any(%s)),
              (select count(*) from seller_membership where identity_user_id = any(%s))
            """,
            (first, first, [first, other_issuer], [first, other_issuer]),
        ).fetchone()
    assert state == ("updated@example.test", False, 0, 0)


def test_runtime_cannot_write_domain_or_idempotency_tables_directly() -> None:
    assert RUNTIME_URL
    statements = (
        """
        insert into identity_user (identity_issuer, identity_sub, email_normalized)
        values ('x:y','abc','a@b.test')
        """,
        """
        insert into seller_organization (slug, display_name, status)
        values ('blocked','Blocked','active')
        """,
        """
        insert into seller_membership
        values (gen_random_uuid(),gen_random_uuid(),'seller_owner','active')
        """,
        """
        insert into app_private.bff_request_replay
        values (gen_random_uuid(),clock_timestamp()+interval '1 minute',clock_timestamp())
        """,
        """
        insert into app_private.seller_onboarding_request
        values (
          gen_random_uuid(), gen_random_uuid(), repeat('0',64),
          gen_random_uuid(), clock_timestamp()
        )
        """,
    )
    with psycopg.connect(RUNTIME_URL) as connection:
        for statement in statements:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement)
            connection.rollback()


def test_bff_request_replay_has_exactly_one_concurrent_winner() -> None:
    assert RUNTIME_URL and ADMIN_URL
    cleanup_request_id = uuid4()
    request_id = uuid4()
    stale_request_id = uuid4()
    expires_at = int(time.time()) + 60

    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            insert into app_private.bff_request_replay (request_id, expires_at, consumed_at)
            values (%s, clock_timestamp() - interval '10 minutes',
                    clock_timestamp() - interval '11 minutes')
            """,
            (stale_request_id,),
        )
        connection.commit()

    # Prove bounded stale cleanup without racing it against transactions that
    # intentionally roll back after losing the unique-key contest below.
    with psycopg.connect(RUNTIME_URL) as connection:
        connection.execute(
            "select app_private.consume_bff_request(%s, %s)",
            (cleanup_request_id, expires_at),
        )
        connection.commit()

    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            "select count(*) from app_private.bff_request_replay where request_id = %s",
            (stale_request_id,),
        ).fetchone() == (0,)

    def attempt() -> bool:
        try:
            with psycopg.connect(RUNTIME_URL) as connection:
                connection.execute(
                    "select app_private.consume_bff_request(%s, %s)",
                    (request_id, expires_at),
                )
                connection.commit()
                return True
        except psycopg.errors.UniqueViolation:
            return False

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(lambda _: attempt(), range(16)))
    assert results.count(True) == 1
    assert results.count(False) == 15

    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.consume_bff_request(%s, %s)",
                (uuid4(), int(time.time()) - 1),
            )


def test_seller_onboarding_is_atomic_idempotent_and_audited() -> None:
    actor = sync_identity()
    key = uuid4()
    body_hash = hashlib.sha256(b'{"name":"Seller Atomic"}').hexdigest()
    slug = f"seller-{uuid4().hex[:12]}"
    seller_id = onboard(actor, slug, "Seller Atomic", key, body_hash)
    assert onboard(actor, slug, "Seller Atomic", key, body_hash) == seller_id

    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        state = connection.execute(
            """
            select
              (select count(*) from seller_organization where id = %s),
              (select count(*) from seller_membership
                where seller_organization_id = %s and identity_user_id = %s
                  and role = 'seller_owner' and status = 'active'),
              (select count(*) from app_private.seller_onboarding_request
                where idempotency_key = %s and body_sha256 = %s),
              (select count(*) from audit_event
                where seller_organization_id = %s and action = 'seller.onboarded')
            """,
            (seller_id, seller_id, actor, key, body_hash, seller_id),
        ).fetchone()
    assert state == (1, 1, 1, 1)

    with pytest.raises(psycopg.errors.UniqueViolation):
        onboard(actor, slug, "Seller Atomic", key, "f" * 64)


def test_concurrent_same_onboarding_key_returns_one_seller() -> None:
    actor = sync_identity()
    key = uuid4()
    body_hash = hashlib.sha256(b"concurrent-onboarding").hexdigest()
    slug = f"seller-{uuid4().hex[:12]}"

    def attempt() -> UUID:
        return onboard(actor, slug, "Concurrent Seller", key, body_hash)

    with ThreadPoolExecutor(max_workers=12) as executor:
        sellers = list(executor.map(lambda _: attempt(), range(12)))
    assert len(set(sellers)) == 1

    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        state = connection.execute(
            """
            select
              (select count(*) from seller_organization where slug = %s),
              (select count(*) from seller_membership where identity_user_id = %s),
              (select count(*) from audit_event
                where actor_identity_user_id = %s and action = 'seller.onboarded')
            """,
            (slug, actor, actor),
        ).fetchone()
    assert state == (1, 1, 1)


def test_concurrent_distinct_keys_for_same_actor_have_one_winner() -> None:
    actor = sync_identity()
    attempts = [
        (
            f"seller-{uuid4().hex[:12]}",
            uuid4(),
            hashlib.sha256(f"distinct-{index}".encode()).hexdigest(),
        )
        for index in range(12)
    ]

    def attempt(input_data: tuple[str, UUID, str]) -> UUID | None:
        slug, key, body_hash = input_data
        try:
            return onboard(actor, slug, "Single Owner Seller", key, body_hash)
        except psycopg.errors.UniqueViolation:
            return None

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(attempt, attempts))
    assert len([result for result in results if result is not None]) == 1

    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        state = connection.execute(
            """
            select
              (select count(*) from seller_membership
                where identity_user_id = %s and role = 'seller_owner'
                  and status = 'active'),
              (select count(*) from app_private.seller_onboarding_request
                where identity_user_id = %s),
              (select count(*) from audit_event
                where actor_identity_user_id = %s and action = 'seller.onboarded')
            """,
            (actor, actor, actor),
        ).fetchone()
    assert state == (1, 1, 1)


def test_onboarding_context_and_validation_fail_closed_without_partial_rows() -> None:
    actor = sync_identity()
    slug = f"seller-{uuid4().hex[:12]}"
    body_hash = hashlib.sha256(b"fail-closed").hexdigest()
    assert RUNTIME_URL and ADMIN_URL

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, actor, None, "seller.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.onboard_seller(%s,%s,%s,%s,%s,%s)",
                (actor, slug, "Denied Seller", uuid4(), body_hash, uuid4()),
            )

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, actor, None, "seller.onboard")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            connection.execute(
                "select app_private.onboard_seller(%s,%s,%s,%s,%s,%s)",
                (actor, "Bad Slug", " Invalid ", uuid4(), body_hash, uuid4()),
            )

    with psycopg.connect(ADMIN_URL) as connection:
        state = connection.execute(
            """
            select
              (select count(*) from seller_organization where slug = %s),
              (select count(*) from seller_membership where identity_user_id = %s),
              (select count(*) from app_private.seller_onboarding_request
                where identity_user_id = %s),
              (select count(*) from audit_event
                where actor_identity_user_id = %s and action = 'seller.onboarded')
            """,
            (slug, actor, actor, actor),
        ).fetchone()
    assert state == (0, 0, 0, 0)


def test_onboarding_rolls_back_every_effect_and_rls_is_capability_aware() -> None:
    actor = sync_identity()
    slug = f"seller-{uuid4().hex[:12]}"
    key = uuid4()
    body_hash = hashlib.sha256(b"rollback-onboarding").hexdigest()
    assert RUNTIME_URL and ADMIN_URL

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, actor, None, "seller.onboard")
        row = connection.execute(
            "select app_private.onboard_seller(%s,%s,%s,%s,%s,%s)",
            (actor, slug, "Rollback Seller", key, body_hash, uuid4()),
        ).fetchone()
        assert row is not None
        connection.rollback()

    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            """
            select
              (select count(*) from seller_organization where slug = %s),
              (select count(*) from seller_membership where identity_user_id = %s),
              (select count(*) from app_private.seller_onboarding_request
                where idempotency_key = %s),
              (select count(*) from audit_event
                where actor_identity_user_id = %s and action = 'seller.onboarded')
            """,
            (slug, actor, key, actor),
        ).fetchone() == (0, 0, 0, 0)

    seller_id = onboard(actor, slug, "Rollback Seller", key, body_hash)
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, actor, None, "seller.list")
        assert connection.execute(
            "select id from seller_organization where id = %s", (seller_id,)
        ).fetchall() == [(seller_id,)]
        assert connection.execute(
            "select seller_organization_id from seller_membership"
        ).fetchall() == [(seller_id,)]
        connection.rollback()

        apply_context(connection, actor, seller_id, "seller.read")
        assert connection.execute(
            "select id from seller_organization where id = %s", (seller_id,)
        ).fetchall() == [(seller_id,)]
        connection.rollback()

        apply_context(connection, actor, seller_id, "audit.read")
        assert connection.execute("select count(*) from seller_organization").fetchone() == (0,)
        # Audit RLS needs the actor's own membership to prove tenant authorization,
        # but it still doesn't expose the seller organization through this capability.
        assert connection.execute("select count(*) from seller_membership").fetchone() == (1,)
        connection.rollback()

        apply_context(connection, actor, seller_id, "identity.read")
        assert connection.execute("select count(*) from seller_organization").fetchone() == (0,)
        assert connection.execute("select count(*) from seller_membership").fetchone() == (0,)


def test_platform_superadmin_catalog_access_is_global_audited_and_tenant_safe() -> None:
    """Regression for #195: global admin access cannot weaken tenant isolation."""

    assert ADMIN_URL and RUNTIME_URL
    owner_a = sync_identity(email=f"owner-a-{uuid4()}@example.test")
    owner_b = sync_identity(email=f"owner-b-{uuid4()}@example.test")
    superadmin = sync_identity(email=f"superadmin-{uuid4()}@example.test")
    buyer = sync_identity(email=f"buyer-{uuid4()}@example.test")
    seller_a = onboard(
        owner_a,
        f"seller-a-{uuid4().hex[:12]}",
        "Seller A",
        uuid4(),
        hashlib.sha256(b"seller-a").hexdigest(),
    )
    seller_b = onboard(
        owner_b,
        f"seller-b-{uuid4().hex[:12]}",
        "Seller B",
        uuid4(),
        hashlib.sha256(b"seller-b").hexdigest(),
    )

    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into platform_grant(identity_user_id,role,status) "
            "values(%s,'platform_superadmin','active')",
            (superadmin,),
        )
        connection.commit()

    product_key = uuid4()
    create_hash = hashlib.sha256(b"platform-superadmin-product").hexdigest()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, owner_b, seller_b, "catalog.draft.create")
        created = connection.execute(
            "select catalog_product_id,course_id from "
            "app_private.create_product_course_draft(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                owner_b,
                seller_b,
                f"product-{uuid4().hex[:12]}",
                "Produto Hub",
                "Descrição original",
                "Curso Hub",
                product_key,
                create_hash,
                uuid4(),
            ),
        ).fetchone()
        assert created is not None
        product_id, course_id = created
        connection.commit()

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, superadmin, None, "seller.list")
        listed = connection.execute(
            "select id,role from app_private.list_accessible_sellers(%s) "
            "where id=any(%s) order by id",
            (superadmin, [seller_a, seller_b]),
        ).fetchall()
        assert listed == sorted(
            [(seller_a, "platform_superadmin"), (seller_b, "platform_superadmin")]
        )
        connection.rollback()

        apply_context(connection, owner_b, seller_b, "catalog.draft.read")
        assert (
            connection.execute(
                "select product.id,organization.slug from catalog_product product "
                "join seller_organization organization "
                "on organization.id=product.seller_organization_id "
                "where product.seller_organization_id=%s and product.id=%s",
                (seller_b, product_id),
            ).fetchone()
            is not None
        )
        connection.rollback()

        apply_context(connection, superadmin, seller_b, "catalog.draft.read")
        assert (
            connection.execute(
                "select product.id,organization.slug from catalog_product product "
                "join seller_organization organization "
                "on organization.id=product.seller_organization_id "
                "where product.seller_organization_id=%s and product.id=%s",
                (seller_b, product_id),
            ).fetchone()
            is not None
        )
        connection.rollback()

        apply_context(connection, owner_a, seller_b, "catalog.draft.read")
        assert (
            connection.execute(
                "select id from catalog_product where seller_organization_id=%s and id=%s",
                (seller_b, product_id),
            ).fetchall()
            == []
        )
        connection.rollback()

        apply_context(connection, buyer, seller_b, "catalog.draft.read")
        assert (
            connection.execute(
                "select id from catalog_product where seller_organization_id=%s and id=%s",
                (seller_b, product_id),
            ).fetchall()
            == []
        )
        connection.rollback()

        for denied_actor in (owner_a, buyer):
            apply_context(connection, denied_actor, seller_b, "catalog.draft.update")
            with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
                connection.execute(
                    "select * from app_private.update_product_course_draft("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        denied_actor,
                        seller_b,
                        product_id,
                        f"product-{uuid4().hex[:12]}",
                        course_id,
                        "Mudança negada",
                        "Não deve persistir",
                        "Curso negado",
                        1,
                        1,
                        uuid4(),
                        hashlib.sha256(b"denied-update").hexdigest(),
                        uuid4(),
                    ),
                )
            connection.rollback()

        apply_context(connection, buyer, seller_b, "catalog.publication.create")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select * from app_private.publish_product(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)",
                (
                    buyer,
                    seller_b,
                    product_id,
                    uuid4(),
                    1,
                    1,
                    f'{{"{course_id}":1}}',
                    uuid4(),
                    hashlib.sha256(b"denied-publication").hexdigest(),
                    uuid4(),
                ),
            )
        connection.rollback()

        update_hash = hashlib.sha256(b"platform-superadmin-update").hexdigest()
        apply_context(connection, superadmin, seller_b, "catalog.draft.update")
        updated = connection.execute(
            "select product_version,course_version,updated from "
            "app_private.update_product_course_draft("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                superadmin,
                seller_b,
                product_id,
                f"product-{uuid4().hex[:12]}",
                course_id,
                "Produto Hub editado",
                "Descrição editada pelo superadmin",
                "Curso Hub editado",
                1,
                1,
                uuid4(),
                update_hash,
                uuid4(),
            ),
        ).fetchone()
        assert updated == (2, 2, True)
        connection.commit()

    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            "select actor_identity_user_id from audit_event "
            "where seller_organization_id=%s and target_id=%s "
            "and action='catalog.product_course_draft_updated' "
            "order by created_at desc limit 1",
            (seller_b, product_id),
        ).fetchone() == (superadmin,)
