from __future__ import annotations

import hashlib
import hmac
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from sqlalchemy.engine import make_url

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
MIGRATOR_URL = os.environ.get("APPSELL_MIGRATOR_DATABASE_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

USER_A = UUID("41000000-0000-4000-8000-000000000001")
USER_B = UUID("41000000-0000-4000-8000-000000000002")
USER_C = UUID("41000000-0000-4000-8000-000000000003")
USER_NONE = UUID("41000000-0000-4000-8000-000000000004")
SELLER_A = UUID("42000000-0000-4000-8000-000000000001")
SELLER_B = UUID("42000000-0000-4000-8000-000000000002")
SELLER_SUSPENDED = UUID("42000000-0000-4000-8000-000000000003")
SELLER_TRANSFER = UUID("42000000-0000-4000-8000-000000000004")
SELLER_RACE = UUID("42000000-0000-4000-8000-000000000005")


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


def body_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_draft(
    actor: UUID,
    seller: UUID,
    slug: str,
    *,
    title: str = "Produto Persistente",
    description: str = "Descrição do rascunho",
    course_title: str = "Curso Inicial",
    idempotency_key: UUID | None = None,
    request_body_hash: str | None = None,
    capability: str = "catalog.draft.create",
) -> tuple[UUID, UUID, bool]:
    assert RUNTIME_URL
    key = idempotency_key or uuid4()
    digest = request_body_hash or body_hash(f"{slug}\0{title}\0{description}\0{course_title}")
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, actor, seller, capability)
        row = connection.execute(
            """
            select catalog_product_id, course_id, created
            from app_private.create_product_course_draft(
              %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                actor,
                seller,
                slug,
                title,
                description,
                course_title,
                key,
                digest,
                uuid4(),
            ),
        ).fetchone()
        assert row is not None
        connection.commit()
    assert isinstance(row[0], UUID)
    assert isinstance(row[1], UUID)
    assert isinstance(row[2], bool)
    return row[0], row[1], row[2]


@pytest.fixture(scope="module", autouse=True)
def seed_catalog_tenants() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            insert into identity_user (
              id, identity_issuer, identity_sub, email_normalized, email_verified
            ) values
              (%s, 'https://identity.catalog.test', 'catalog-user-a',
                'catalog-a@example.test', true),
              (%s, 'https://identity.catalog.test', 'catalog-user-b',
                'catalog-b@example.test', true),
              (%s, 'https://identity.catalog.test', 'catalog-user-c',
                'catalog-c@example.test', true),
              (%s, 'https://identity.catalog.test', 'catalog-user-none',
                'catalog-none@example.test', true)
            """,
            (USER_A, USER_B, USER_C, USER_NONE),
        )
        connection.execute(
            """
            insert into seller_organization (id, slug, display_name, status)
            values
              (%s, 'catalog-seller-a', 'Catalog Seller A', 'active'),
              (%s, 'catalog-seller-b', 'Catalog Seller B', 'active'),
              (%s, 'catalog-suspended', 'Catalog Suspended', 'suspended'),
              (%s, 'catalog-transfer', 'Catalog Transfer', 'active'),
              (%s, 'catalog-race', 'Catalog Race', 'active')
            """,
            (SELLER_A, SELLER_B, SELLER_SUSPENDED, SELLER_TRANSFER, SELLER_RACE),
        )
        connection.execute(
            """
            insert into seller_membership (
              seller_organization_id, identity_user_id, role, status
            ) values
              (%s, %s, 'seller_owner', 'active'),
              (%s, %s, 'seller_owner', 'active'),
              (%s, %s, 'seller_owner', 'active'),
              (%s, %s, 'seller_owner', 'active'),
              (%s, %s, 'seller_owner', 'active')
            """,
            (
                SELLER_A,
                USER_A,
                SELLER_B,
                USER_B,
                SELLER_SUSPENDED,
                USER_C,
                SELLER_TRANSFER,
                USER_A,
                SELLER_RACE,
                USER_A,
            ),
        )
        connection.commit()


def test_schema_roles_rls_and_runtime_privileges_are_minimal() -> None:
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        assert connection.execute("select app_private.schema_ready()").fetchone() == (True,)

    with psycopg.connect(ADMIN_URL) as connection:
        tables = connection.execute(
            """
            select c.relname, owner.rolname, c.relrowsecurity, c.relforcerowsecurity
            from pg_class c
            join pg_namespace namespace on namespace.oid = c.relnamespace
            join pg_roles owner on owner.oid = c.relowner
            where namespace.nspname = 'public'
              and c.relname in ('catalog_product', 'course', 'product_course')
            order by c.relname
            """
        ).fetchall()
        privileges = connection.execute(
            """
            select
              (select version_num from alembic_version),
              has_table_privilege('appsell_runtime', 'catalog_product', 'SELECT'),
              has_table_privilege('appsell_runtime', 'catalog_product', 'INSERT'),
              has_table_privilege('appsell_runtime', 'course', 'UPDATE'),
              has_table_privilege('appsell_runtime', 'product_course', 'DELETE'),
              has_table_privilege(
                'appsell_runtime', 'app_private.product_draft_request', 'SELECT'
              ),
              has_table_privilege(
                'appsell_runtime', 'app_private.product_draft_request', 'INSERT'
              ),
              has_function_privilege(
                'appsell_runtime',
                'app_private.create_product_course_draft(uuid,uuid,text,text,text,text,uuid,text,uuid)',
                'EXECUTE'
              ),
              has_function_privilege(
                'appsell_runtime',
                'app_private.catalog_draft_read_allowed(uuid)',
                'EXECUTE'
              )
            """
        ).fetchone()

    assert tables == [
        ("catalog_product", "appsell_owner", True, True),
        ("course", "appsell_owner", True, True),
        ("product_course", "appsell_owner", True, True),
    ]
    assert privileges == (
        "0009_buyer_multi_seller",
        True,
        False,
        False,
        False,
        False,
        False,
        True,
        True,
    )


def test_owner_creates_atomic_draft_and_rls_isolates_sellers() -> None:
    slug = f"owner-{uuid4().hex[:12]}"
    product_id, course_id, created = create_draft(USER_A, SELLER_A, slug)
    assert created is True

    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_A, "catalog.draft.read")
        assert connection.execute(
            """
            select product.id, linked.course_id, product.status, course.status
            from catalog_product product
            join product_course linked
              on linked.seller_organization_id = product.seller_organization_id
             and linked.catalog_product_id = product.id
            join course
              on course.seller_organization_id = linked.seller_organization_id
             and course.id = linked.course_id
            where product.id = %s
            """,
            (product_id,),
        ).fetchall() == [(product_id, course_id, "draft", "draft")]
        connection.rollback()

        apply_context(connection, USER_B, SELLER_B, "catalog.draft.read")
        assert (
            connection.execute(
                "select id from catalog_product where id = %s", (product_id,)
            ).fetchall()
            == []
        )
        assert (
            connection.execute("select id from course where id = %s", (course_id,)).fetchall() == []
        )
        connection.rollback()

        apply_context(connection, USER_A, SELLER_B, "catalog.draft.read")
        assert connection.execute("select count(*) from catalog_product").fetchone() == (0,)


def test_no_context_wrong_capability_and_unauthorized_owner_fail_closed() -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        assert connection.execute("select count(*) from catalog_product").fetchone() == (0,)
        apply_context(connection, USER_A, SELLER_A, "seller.read")
        assert connection.execute("select count(*) from catalog_product").fetchone() == (0,)

    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        create_draft(
            USER_A,
            SELLER_A,
            f"wrong-cap-{uuid4().hex[:8]}",
            capability="catalog.draft.read",
        )
    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        create_draft(USER_NONE, SELLER_A, f"no-member-{uuid4().hex[:8]}")
    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        create_draft(USER_C, SELLER_SUSPENDED, f"suspended-{uuid4().hex[:8]}")


def test_runtime_cannot_write_domain_or_receipt_tables_directly() -> None:
    assert RUNTIME_URL
    statements = (
        """
        insert into catalog_product (seller_organization_id, slug, title)
        values ('42000000-0000-4000-8000-000000000001', 'blocked-product', 'Blocked')
        """,
        """
        insert into course (seller_organization_id, title)
        values ('42000000-0000-4000-8000-000000000001', 'Blocked')
        """,
        """
        insert into product_course (
          seller_organization_id, catalog_product_id, course_id
        ) values (
          '42000000-0000-4000-8000-000000000001', gen_random_uuid(), gen_random_uuid()
        )
        """,
        """
        insert into app_private.product_draft_request (
          seller_organization_id, idempotency_key, identity_user_id,
          body_sha256, catalog_product_id, course_id
        ) values (
          '42000000-0000-4000-8000-000000000001', gen_random_uuid(),
          '41000000-0000-4000-8000-000000000001', repeat('0', 64),
          gen_random_uuid(), gen_random_uuid()
        )
        """,
    )
    with psycopg.connect(RUNTIME_URL) as connection:
        for statement in statements:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement)
            connection.rollback()


def test_composite_foreign_keys_reject_cross_tenant_product_course() -> None:
    assert ADMIN_URL
    product_id = uuid4()
    course_id = uuid4()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            insert into catalog_product (seller_organization_id, id, slug, title)
            values (%s, %s, %s, 'Cross Tenant Product')
            """,
            (SELLER_A, product_id, f"cross-{uuid4().hex[:10]}"),
        )
        connection.execute(
            """
            insert into course (seller_organization_id, id, title)
            values (%s, %s, 'Cross Tenant Course')
            """,
            (SELLER_B, course_id),
        )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                """
                insert into product_course (
                  seller_organization_id, catalog_product_id, course_id
                ) values (%s, %s, %s)
                """,
                (SELLER_A, product_id, course_id),
            )


def test_retry_is_stable_and_body_or_actor_mismatch_conflicts() -> None:
    key = uuid4()
    digest = body_hash("stable-request")
    slug = f"retry-{uuid4().hex[:12]}"
    first = create_draft(
        USER_A,
        SELLER_TRANSFER,
        slug,
        idempotency_key=key,
        request_body_hash=digest,
    )
    repeated = create_draft(
        USER_A,
        SELLER_TRANSFER,
        slug,
        idempotency_key=key,
        request_body_hash=digest,
    )
    assert repeated == (first[0], first[1], False)

    with pytest.raises(psycopg.errors.UniqueViolation):
        create_draft(
            USER_A,
            SELLER_TRANSFER,
            slug,
            idempotency_key=key,
            request_body_hash=body_hash("different-request"),
        )

    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            update seller_membership set status = 'revoked', updated_at = clock_timestamp()
            where seller_organization_id = %s and identity_user_id = %s
            """,
            (SELLER_TRANSFER, USER_A),
        )
        connection.execute(
            """
            insert into seller_membership (
              seller_organization_id, identity_user_id, role, status
            ) values (%s, %s, 'seller_owner', 'active')
            """,
            (SELLER_TRANSFER, USER_C),
        )
        connection.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):
        create_draft(
            USER_C,
            SELLER_TRANSFER,
            slug,
            idempotency_key=key,
            request_body_hash=digest,
        )


def test_sixteen_concurrent_retries_create_one_graph_and_one_audit() -> None:
    key = uuid4()
    digest = body_hash("sixteen-concurrent-retries")
    slug = f"concurrent-{uuid4().hex[:10]}"

    def attempt(_: int) -> tuple[UUID, UUID, bool]:
        return create_draft(
            USER_A,
            SELLER_A,
            slug,
            idempotency_key=key,
            request_body_hash=digest,
        )

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(attempt, range(16)))

    assert len({(result[0], result[1]) for result in results}) == 1
    assert [result[2] for result in results].count(True) == 1
    assert [result[2] for result in results].count(False) == 15
    product_id, course_id, _ = results[0]

    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        state = connection.execute(
            """
            select
              (select count(*) from catalog_product
                where seller_organization_id = %s and id = %s),
              (select count(*) from course
                where seller_organization_id = %s and id = %s),
              (select count(*) from product_course
                where seller_organization_id = %s and catalog_product_id = %s
                  and course_id = %s),
              (select count(*) from app_private.product_draft_request
                where seller_organization_id = %s and idempotency_key = %s),
              (select count(*) from audit_event
                where seller_organization_id = %s and target_id = %s
                  and action = 'catalog.product_draft_created')
            """,
            (
                SELLER_A,
                product_id,
                SELLER_A,
                course_id,
                SELLER_A,
                product_id,
                course_id,
                SELLER_A,
                key,
                SELLER_A,
                product_id,
            ),
        ).fetchone()
    assert state == (1, 1, 1, 1, 1)


def test_concurrent_owner_revocation_is_linearized_with_draft_creation() -> None:
    assert ADMIN_URL and RUNTIME_URL
    slug = f"revoke-race-{uuid4().hex[:10]}"
    key = uuid4()
    digest = body_hash("owner-revocation-race")

    def revoke_owner() -> None:
        assert ADMIN_URL
        with psycopg.connect(ADMIN_URL) as connection:
            connection.execute(
                """
                update seller_membership
                set status = 'revoked', updated_at = clock_timestamp()
                where seller_organization_id = %s and identity_user_id = %s
                """,
                (SELLER_RACE, USER_A),
            )
            connection.commit()

    with psycopg.connect(RUNTIME_URL) as creator:
        apply_context(creator, USER_A, SELLER_RACE, "catalog.draft.create")
        row = creator.execute(
            """
            select catalog_product_id, course_id, created
            from app_private.create_product_course_draft(
              %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                USER_A,
                SELLER_RACE,
                slug,
                "Linearized Product",
                "Created before the revocation commits",
                "Linearized Course",
                key,
                digest,
                uuid4(),
            ),
        ).fetchone()
        assert row is not None and row[2] is True

        with ThreadPoolExecutor(max_workers=1) as executor:
            revocation = executor.submit(revoke_owner)
            time.sleep(0.2)
            assert not revocation.done(), "revocation bypassed the authorization row lock"
            creator.commit()
            revocation.result(timeout=5)

    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        create_draft(USER_A, SELLER_RACE, f"after-revoke-{uuid4().hex[:8]}")


def test_slug_collision_rolls_back_course_receipt_and_audit() -> None:
    slug = f"collision-{uuid4().hex[:10]}"
    first_product, first_course, _ = create_draft(USER_B, SELLER_B, slug)
    before_request = uuid4()
    with pytest.raises(psycopg.errors.UniqueViolation):
        create_draft(
            USER_B,
            SELLER_B,
            slug,
            title="Second Product",
            course_title="Orphan Must Roll Back",
            idempotency_key=before_request,
            request_body_hash=body_hash("slug-collision-second-attempt"),
        )

    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        state = connection.execute(
            """
            select
              (select count(*) from catalog_product
                where seller_organization_id = %s and slug = %s),
              (select count(*) from course
                where seller_organization_id = %s
                  and title = 'Orphan Must Roll Back'),
              (select count(*) from app_private.product_draft_request
                where seller_organization_id = %s and idempotency_key = %s),
              (select count(*) from audit_event
                where seller_organization_id = %s
                  and action = 'catalog.product_draft_created'
                  and metadata ->> 'slug' = %s),
              (select count(*) from product_course
                where seller_organization_id = %s and catalog_product_id = %s
                  and course_id = %s)
            """,
            (
                SELLER_B,
                slug,
                SELLER_B,
                SELLER_B,
                before_request,
                SELLER_B,
                slug,
                SELLER_B,
                first_product,
                first_course,
            ),
        ).fetchone()
    assert state == (1, 0, 0, 1, 1)


def test_transaction_context_is_cleared_before_connection_reuse() -> None:
    slug = f"pool-{uuid4().hex[:12]}"
    product_id, _, _ = create_draft(USER_A, SELLER_A, slug)
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_A, "catalog.draft.read")
        assert connection.execute(
            "select id from catalog_product where id = %s", (product_id,)
        ).fetchall() == [(product_id,)]
        connection.commit()
        assert (
            connection.execute(
                "select id from catalog_product where id = %s", (product_id,)
            ).fetchall()
            == []
        )


@pytest.mark.skipif(not MIGRATOR_URL, reason="migrator URL is required for N-1 upgrade")
def test_upgrade_from_0002_preserves_existing_tenant() -> None:
    assert ADMIN_URL and MIGRATOR_URL
    database_name = f"appsell_upgrade_{uuid4().hex}"
    maintenance_url = make_conninfo(ADMIN_URL, dbname="postgres")
    target_url = make_conninfo(ADMIN_URL, dbname=database_name)
    migrator_url = (
        make_url(MIGRATOR_URL).set(database=database_name).render_as_string(hide_password=False)
    )
    repository_root = Path(__file__).resolve().parents[3]

    with psycopg.connect(maintenance_url, autocommit=True) as connection:
        connection.execute(sql.SQL("create database {}").format(sql.Identifier(database_name)))
        connection.execute(
            sql.SQL("alter database {} owner to appsell_owner").format(
                sql.Identifier(database_name)
            )
        )
        connection.execute(
            sql.SQL("revoke connect on database {} from public").format(
                sql.Identifier(database_name)
            )
        )
        connection.execute(
            sql.SQL("grant connect on database {} to appsell_migrator, appsell_runtime").format(
                sql.Identifier(database_name)
            )
        )

    environment = {**os.environ, "APPSELL_MIGRATOR_DATABASE_URL": migrator_url}
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "apps/api/alembic.ini",
                "upgrade",
                "0002_bff_api_onboarding",
            ],
            cwd=repository_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        preserved_seller = uuid4()
        preserved_identity = uuid4()
        with psycopg.connect(target_url) as connection:
            connection.execute(
                """
                insert into identity_user (
                  id, identity_issuer, identity_sub, email_normalized, email_verified
                ) values (%s, 'https://identity.upgrade.test', 'upgrade-owner',
                          'upgrade@example.test', true)
                """,
                (preserved_identity,),
            )
            connection.execute(
                """
                insert into seller_organization (id, slug, display_name, status)
                values (%s, 'upgrade-seller', 'Upgrade Seller', 'active')
                """,
                (preserved_seller,),
            )
            connection.execute(
                """
                insert into seller_membership (
                  seller_organization_id, identity_user_id, role, status
                ) values (%s, %s, 'seller_owner', 'active')
                """,
                (preserved_seller, preserved_identity),
            )
            connection.commit()

        subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "apps/api/alembic.ini",
                "upgrade",
                "head",
            ],
            cwd=repository_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        with psycopg.connect(target_url) as connection:
            state = connection.execute(
                """
                select
                  (select version_num from alembic_version),
                  (select count(*) from seller_organization where id = %s),
                  to_regclass('public.catalog_product') is not null,
                  to_regclass('app_private.product_draft_request') is not null
                """,
                (preserved_seller,),
            ).fetchone()
        assert state == ("0009_buyer_multi_seller", 1, True, True)
    finally:
        with psycopg.connect(maintenance_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("drop database if exists {} with (force)").format(
                    sql.Identifier(database_name)
                )
            )
