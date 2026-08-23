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
from psycopg.types.json import Jsonb
from sqlalchemy.engine import make_url

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
MIGRATOR_URL = os.environ.get("APPSELL_MIGRATOR_DATABASE_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

USER_A = UUID("61000000-0000-4000-8000-000000000001")
USER_B = UUID("61000000-0000-4000-8000-000000000002")


def sign_context(actor: UUID, seller: UUID, capability: str, issued: int, expires: int) -> str:
    payload = f"{actor}:{seller}:{capability}:{issued}:{expires}"
    return hmac.new(
        bytes.fromhex(CONTEXT_KEY_HEX or ""), payload.encode(), hashlib.sha256
    ).hexdigest()


def apply_context(
    connection: psycopg.Connection[tuple[object, ...]],
    actor: UUID,
    seller: UUID,
    capability: str,
) -> None:
    issued = int(time.time())
    expires = issued + 60
    connection.execute(
        "select app_private.apply_request_context(%s, %s, %s, %s, %s, %s)",
        (
            actor,
            seller,
            capability,
            issued,
            expires,
            sign_context(actor, seller, capability, issued, expires),
        ),
    )


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture(scope="module", autouse=True)
def seed_identities() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            insert into identity_user (
              id, identity_issuer, identity_sub, email_normalized, email_verified
            ) values
              (%s, 'https://identity.publication.test', 'publication-user-a',
                'publication-a@example.test', true),
              (%s, 'https://identity.publication.test', 'publication-user-b',
                'publication-b@example.test', true)
            """,
            (USER_A, USER_B),
        )
        connection.commit()


def create_graph(actor: UUID = USER_A, *, seller_status: str = "active") -> tuple[UUID, UUID, UUID]:
    assert ADMIN_URL
    seller_id, product_id, course_id = uuid4(), uuid4(), uuid4()
    suffix = uuid4().hex[:12]
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            insert into seller_organization (id, slug, display_name, status)
            values (%s, %s, %s, %s)
            """,
            (seller_id, f"publication-{suffix}", f"Publication {suffix}", seller_status),
        )
        connection.execute(
            """
            insert into seller_membership (
              seller_organization_id, identity_user_id, role, status
            ) values (%s, %s, 'seller_owner', 'active')
            """,
            (seller_id, actor),
        )
        connection.execute(
            """
            insert into catalog_product (
              seller_organization_id, id, slug, title, description
            ) values (%s, %s, %s, 'Produto Base', 'Descrição Base')
            """,
            (seller_id, product_id, f"product-{suffix}"),
        )
        connection.execute(
            """
            insert into course (seller_organization_id, id, title, description)
            values (%s, %s, 'Curso Base', 'Conteúdo Base')
            """,
            (seller_id, course_id),
        )
        connection.execute(
            """
            insert into product_course (
              seller_organization_id, catalog_product_id, course_id, position
            ) values (%s, %s, %s, 0)
            """,
            (seller_id, product_id, course_id),
        )
        connection.commit()
    return seller_id, product_id, course_id


def add_product_graph(seller_id: UUID) -> tuple[UUID, UUID]:
    assert ADMIN_URL
    product_id, course_id = uuid4(), uuid4()
    suffix = uuid4().hex[:12]
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            insert into catalog_product (
              seller_organization_id, id, slug, title, description
            ) values (%s, %s, %s, 'Produto Paralelo', 'Descrição Paralela')
            """,
            (seller_id, product_id, f"parallel-{suffix}"),
        )
        connection.execute(
            """
            insert into course (seller_organization_id, id, title, description)
            values (%s, %s, 'Curso Paralelo', 'Conteúdo Paralelo')
            """,
            (seller_id, course_id),
        )
        connection.execute(
            """
            insert into product_course (
              seller_organization_id, catalog_product_id, course_id, position
            ) values (%s, %s, %s, 0)
            """,
            (seller_id, product_id, course_id),
        )
        connection.commit()
    return product_id, course_id


def update_draft(
    actor: UUID,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    offer_id: UUID,
    *,
    expected_product_version: int = 1,
    expected_course_version: int = 1,
    expected_offer_version: int = 0,
    key: UUID | None = None,
    body_sha256: str | None = None,
    product_title: str = "Produto Editado",
    offer_unit_amount_minor: int | None = 19900,
    offer_currency: str | None = "BRL",
    offer_billing_period: str | None = "one_time",
) -> tuple[UUID, int, int, int, bool]:
    assert RUNTIME_URL
    request_key = key or uuid4()
    request_digest = body_sha256 or digest(
        f"{product_id}:{course_id}:{offer_id}:{product_title}:{expected_product_version}"
    )
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, actor, seller_id, "catalog.draft.update")
        row = connection.execute(
            """
            select offer_id, product_version, course_version, offer_version, created
            from app_private.update_product_course_offer_draft(
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                actor,
                seller_id,
                product_id,
                f"edited-{product_id.hex[:12]}",
                course_id,
                offer_id,
                product_title,
                "Descrição editada",
                "Curso Editado",
                "Oferta Informativa",
                "Checkout externo ainda não configurado",
                offer_unit_amount_minor,
                offer_currency,
                offer_billing_period,
                expected_product_version,
                expected_course_version,
                expected_offer_version,
                request_key,
                request_digest,
                uuid4(),
            ),
        ).fetchone()
        assert row is not None
        connection.commit()
    assert isinstance(row[0], UUID)
    return row[0], int(row[1]), int(row[2]), int(row[3]), bool(row[4])


def publish(
    actor: UUID,
    seller_id: UUID,
    product_id: UUID,
    course_id: UUID,
    offer_id: UUID,
    *,
    product_version: int,
    course_version: int,
    offer_version: int,
    key: UUID | None = None,
    body_sha256: str | None = None,
) -> tuple[UUID, int, int, str, bool]:
    assert RUNTIME_URL
    request_key = key or uuid4()
    request_digest = body_sha256 or digest(
        f"publish:{product_id}:{product_version}:{course_version}:{offer_version}"
    )
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, actor, seller_id, "catalog.publication.create")
        row = connection.execute(
            """
            select publication_id, publication_version, product_version,
                   source_sha256, created
            from app_private.publish_product(
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                actor,
                seller_id,
                product_id,
                offer_id,
                product_version,
                offer_version,
                Jsonb({str(course_id): course_version}),
                request_key,
                request_digest,
                uuid4(),
            ),
        ).fetchone()
        assert row is not None
        connection.commit()
    assert isinstance(row[0], UUID)
    return row[0], int(row[1]), int(row[2]), str(row[3]), bool(row[4])


def read_offer(seller_id: UUID, offer_id: UUID) -> tuple[object, ...] | None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        return connection.execute(
            "select unit_amount_minor, rtrim(currency), billing_period, version "
            "from offer where seller_organization_id = %s and id = %s",
            (seller_id, offer_id),
        ).fetchone()


def read_offer_status(seller_id: UUID, offer_id: UUID) -> tuple[object, ...] | None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        return connection.execute(
            "select status, version from offer where seller_organization_id = %s and id = %s",
            (seller_id, offer_id),
        ).fetchone()


def transition_offer_status(
    actor: UUID,
    seller_id: UUID,
    product_id: UUID,
    offer_id: UUID,
    expected_status: str,
    target_status: str,
    expected_offer_version: int,
    *,
    key: UUID | None = None,
    body_sha256: str | None = None,
) -> tuple[str, int, bool]:
    assert RUNTIME_URL
    request_key = key or uuid4()
    request_digest = body_sha256 or digest(
        f"{product_id}:{offer_id}:{target_status}:{expected_offer_version}"
    )
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, actor, seller_id, "catalog.offer.status.update")
        row = connection.execute(
            """
            select offer_status, offer_version, changed
            from app_private.transition_offer_status(
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                actor,
                seller_id,
                product_id,
                offer_id,
                expected_status,
                target_status,
                expected_offer_version,
                request_key,
                request_digest,
                uuid4(),
            ),
        ).fetchone()
        assert row is not None
        connection.commit()
    return str(row[0]), int(row[1]), bool(row[2])


def test_offer_pricing_is_persisted_on_create_and_update() -> None:
    seller_id, product_id, course_id = create_graph()
    offer_id = uuid4()
    _, product_version, course_version, offer_version, created = update_draft(
        USER_A,
        seller_id,
        product_id,
        course_id,
        offer_id,
        offer_unit_amount_minor=19900,
        offer_currency="BRL",
        offer_billing_period="one_time",
    )
    assert created is True
    assert offer_version == 1
    assert read_offer(seller_id, offer_id) == (19900, "BRL", "one_time", 1)

    _, _, _, updated_offer_version, _ = update_draft(
        USER_A,
        seller_id,
        product_id,
        course_id,
        offer_id,
        expected_product_version=product_version,
        expected_course_version=course_version,
        expected_offer_version=offer_version,
        offer_unit_amount_minor=250000,
        offer_currency="USD",
        offer_billing_period="monthly",
    )
    assert updated_offer_version == 2
    assert read_offer(seller_id, offer_id) == (250000, "USD", "monthly", 2)


def test_offer_pricing_is_preserved_when_omitted_on_update() -> None:
    seller_id, product_id, course_id = create_graph()
    offer_id = uuid4()
    _, product_version, course_version, offer_version, _ = update_draft(
        USER_A,
        seller_id,
        product_id,
        course_id,
        offer_id,
        offer_unit_amount_minor=39900,
        offer_currency="USD",
        offer_billing_period="monthly",
    )
    assert read_offer(seller_id, offer_id) == (39900, "USD", "monthly", 1)

    # An editorial update that omits price (NULL) must preserve the stored price
    # instead of silently resetting it to the create-time defaults.
    _, _, _, preserved_version, _ = update_draft(
        USER_A,
        seller_id,
        product_id,
        course_id,
        offer_id,
        expected_product_version=product_version,
        expected_course_version=course_version,
        expected_offer_version=offer_version,
        offer_unit_amount_minor=None,
        offer_currency=None,
        offer_billing_period=None,
    )
    assert preserved_version == 2
    assert read_offer(seller_id, offer_id) == (39900, "USD", "monthly", 2)


def test_offer_pricing_defaults_when_omitted_on_create() -> None:
    seller_id, product_id, course_id = create_graph()
    offer_id = uuid4()
    _, _, _, offer_version, created = update_draft(
        USER_A,
        seller_id,
        product_id,
        course_id,
        offer_id,
        offer_unit_amount_minor=None,
        offer_currency=None,
        offer_billing_period=None,
    )
    assert created is True
    assert offer_version == 1
    assert read_offer(seller_id, offer_id) == (0, "BRL", "one_time", 1)


def test_offer_status_valid_transitions_are_versioned_and_idempotent() -> None:
    seller_id, product_id, course_id = create_graph()
    offer_id = uuid4()
    update_draft(USER_A, seller_id, product_id, course_id, offer_id)
    publish(
        USER_A,
        seller_id,
        product_id,
        course_id,
        offer_id,
        product_version=2,
        course_version=2,
        offer_version=1,
    )
    assert read_offer_status(seller_id, offer_id) == ("draft", 1)

    activation_key = uuid4()
    activation_hash = digest(f"activate:{offer_id}")
    assert transition_offer_status(
        USER_A,
        seller_id,
        product_id,
        offer_id,
        "draft",
        "active",
        1,
        key=activation_key,
        body_sha256=activation_hash,
    ) == ("active", 2, True)
    assert transition_offer_status(
        USER_A,
        seller_id,
        product_id,
        offer_id,
        "draft",
        "active",
        1,
        key=activation_key,
        body_sha256=activation_hash,
    ) == ("active", 2, False)
    assert transition_offer_status(
        USER_A, seller_id, product_id, offer_id, "active", "archived", 2
    ) == ("archived", 3, True)
    assert read_offer_status(seller_id, offer_id) == ("archived", 3)


def test_offer_status_allows_active_to_draft_round_trip() -> None:
    # A seller can revert an active offer to draft (to edit and re-publish),
    # then re-activate it. Each transition bumps the version.
    seller_id, product_id, course_id = create_graph()
    offer_id = uuid4()
    update_draft(USER_A, seller_id, product_id, course_id, offer_id)
    publish(
        USER_A,
        seller_id,
        product_id,
        course_id,
        offer_id,
        product_version=2,
        course_version=2,
        offer_version=1,
    )

    assert transition_offer_status(
        USER_A, seller_id, product_id, offer_id, "draft", "active", 1
    ) == ("active", 2, True)
    assert transition_offer_status(
        USER_A, seller_id, product_id, offer_id, "active", "draft", 2
    ) == ("draft", 3, True)
    assert read_offer_status(seller_id, offer_id) == ("draft", 3)
    # Re-activation from the reverted draft still works and requires the price.
    assert transition_offer_status(
        USER_A, seller_id, product_id, offer_id, "draft", "active", 3
    ) == ("active", 4, True)
    assert read_offer_status(seller_id, offer_id) == ("active", 4)


@pytest.mark.parametrize(
    ("initial_status", "target_status"),
    [
        ("draft", "archived"),
        ("active", "active"),
        ("archived", "active"),
        ("archived", "draft"),
        ("archived", "archived"),
    ],
)
def test_offer_status_rejects_invalid_transitions(initial_status: str, target_status: str) -> None:
    seller_id, product_id, course_id = create_graph()
    offer_id = uuid4()
    update_draft(USER_A, seller_id, product_id, course_id, offer_id)
    version = 1
    if initial_status in {"active", "archived"}:
        publish(
            USER_A,
            seller_id,
            product_id,
            course_id,
            offer_id,
            product_version=2,
            course_version=2,
            offer_version=1,
        )
        transition_offer_status(USER_A, seller_id, product_id, offer_id, "draft", "active", version)
        version = 2
    if initial_status == "archived":
        transition_offer_status(
            USER_A, seller_id, product_id, offer_id, "active", "archived", version
        )
        version = 3

    with pytest.raises(psycopg.Error) as error:
        transition_offer_status(
            USER_A,
            seller_id,
            product_id,
            offer_id,
            initial_status,
            target_status,
            version,
        )
    assert error.value.sqlstate in {"22023", "P4222"}
    assert read_offer_status(seller_id, offer_id) == (initial_status, version)


def test_offer_status_activation_requires_positive_price() -> None:
    seller_id, product_id, course_id = create_graph()
    offer_id = uuid4()
    update_draft(
        USER_A,
        seller_id,
        product_id,
        course_id,
        offer_id,
        offer_unit_amount_minor=0,
    )
    publish(
        USER_A,
        seller_id,
        product_id,
        course_id,
        offer_id,
        product_version=2,
        course_version=2,
        offer_version=1,
    )
    with pytest.raises(psycopg.Error) as error:
        transition_offer_status(USER_A, seller_id, product_id, offer_id, "draft", "active", 1)
    assert error.value.sqlstate == "P4222"
    assert read_offer_status(seller_id, offer_id) == ("draft", 1)


def test_offer_status_activation_requires_published_catalog() -> None:
    seller_id, product_id, course_id = create_graph()
    offer_id = uuid4()
    update_draft(
        USER_A,
        seller_id,
        product_id,
        course_id,
        offer_id,
        offer_unit_amount_minor=19900,
    )
    with pytest.raises(psycopg.Error) as error:
        transition_offer_status(USER_A, seller_id, product_id, offer_id, "draft", "active", 1)
    assert error.value.sqlstate == "55000"
    assert read_offer_status(seller_id, offer_id) == ("draft", 1)

    publish(
        USER_A,
        seller_id,
        product_id,
        course_id,
        offer_id,
        product_version=2,
        course_version=2,
        offer_version=1,
    )
    assert transition_offer_status(
        USER_A, seller_id, product_id, offer_id, "draft", "active", 1
    ) == ("active", 2, True)


def test_offer_status_check_rejects_unknown_values() -> None:
    seller_id, product_id, course_id = create_graph()
    offer_id = uuid4()
    update_draft(USER_A, seller_id, product_id, course_id, offer_id)
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection, pytest.raises(psycopg.Error) as error:
        connection.execute(
            "update offer set status = 'paused' where seller_organization_id = %s and id = %s",
            (seller_id, offer_id),
        )
    assert error.value.sqlstate == "23514"
    assert read_offer_status(seller_id, offer_id) == ("draft", 1)


@pytest.mark.parametrize(
    ("amount", "currency", "period"),
    [
        (-1, "BRL", "one_time"),
        (100_000_000_000, "BRL", "one_time"),
        (1000, "brl", "one_time"),
        (1000, "BRLL", "one_time"),
        (1000, "BR", "one_time"),
        (1000, "BRL", "weekly"),
    ],
)
def test_offer_pricing_rejects_invalid_values(amount: int, currency: str, period: str) -> None:
    seller_id, product_id, course_id = create_graph()
    offer_id = uuid4()
    with pytest.raises(psycopg.Error) as error:
        update_draft(
            USER_A,
            seller_id,
            product_id,
            course_id,
            offer_id,
            offer_unit_amount_minor=amount,
            offer_currency=currency,
            offer_billing_period=period,
        )
    assert error.value.sqlstate == "22023"
    assert read_offer(seller_id, offer_id) is None


def test_offer_pricing_is_isolated_across_tenants() -> None:
    seller_a, product_a, course_a = create_graph()
    seller_b, _, _ = create_graph(USER_B)
    offer_a = uuid4()
    update_draft(
        USER_A,
        seller_a,
        product_a,
        course_a,
        offer_a,
        offer_unit_amount_minor=77700,
        offer_currency="BRL",
        offer_billing_period="yearly",
    )
    assert read_offer(seller_a, offer_a) == (77700, "BRL", "yearly", 1)
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_B, seller_b, "catalog.draft.preview")
        leaked = connection.execute(
            "select count(*) from offer where id = %s", (offer_a,)
        ).fetchone()
    assert leaked == (0,)


def test_schema_rls_and_runtime_privileges_are_minimal() -> None:
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
              and c.relname in ('offer', 'publication')
            order by c.relname
            """
        ).fetchall()
        privileges = connection.execute(
            """
            select
              (select version_num from alembic_version),
              has_table_privilege('appsell_runtime', 'offer', 'SELECT'),
              has_table_privilege('appsell_runtime', 'offer', 'UPDATE'),
              has_table_privilege('appsell_runtime', 'publication', 'SELECT'),
              has_table_privilege('appsell_runtime', 'publication', 'INSERT'),
              has_table_privilege(
                'appsell_runtime', 'app_private.catalog_mutation_request', 'SELECT'
              )
            """
        ).fetchone()
        membership_key = connection.execute(
            """
            select pg_get_constraintdef(oid)
            from pg_constraint
            where conrelid = 'seller_membership'::regclass
              and conname = 'seller_membership_authorization_state_key'
            """
        ).fetchone()
    assert tables == [
        ("offer", "appsell_owner", True, True),
        ("publication", "appsell_owner", True, True),
    ]
    assert privileges == (
        "0032_platform_superadmin_catalog",
        True,
        False,
        True,
        False,
        False,
    )
    assert membership_key == ("UNIQUE (seller_organization_id, identity_user_id, role, status)",)


def test_atomic_update_preview_and_cross_tenant_rls() -> None:
    seller_a, product_id, course_id = create_graph()
    seller_b, _, _ = create_graph(USER_B)
    offer_id = uuid4()
    assert update_draft(USER_A, seller_a, product_id, course_id, offer_id)[1:] == (
        2,
        2,
        1,
        True,
    )
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, seller_a, "catalog.draft.preview")
        state = connection.execute(
            """
            select product.title, course.title, offer.title, offer.version
            from catalog_product product
            join product_course linked
              on linked.seller_organization_id = product.seller_organization_id
             and linked.catalog_product_id = product.id
            join course
              on course.seller_organization_id = linked.seller_organization_id
             and course.id = linked.course_id
            join offer
              on offer.seller_organization_id = product.seller_organization_id
             and offer.catalog_product_id = product.id
            where product.id = %s
            """,
            (product_id,),
        ).fetchone()
        assert state == ("Produto Editado", "Curso Editado", "Oferta Informativa", 1)
        connection.rollback()
        apply_context(connection, USER_B, seller_b, "catalog.draft.preview")
        assert connection.execute(
            "select count(*) from offer where id = %s", (offer_id,)
        ).fetchone() == (0,)


def test_update_conflict_rolls_back_and_single_offer_is_enforced() -> None:
    seller_id, product_id, course_id = create_graph()
    offer_id = uuid4()
    update_draft(USER_A, seller_id, product_id, course_id, offer_id)
    with pytest.raises(psycopg.errors.SerializationFailure):
        update_draft(
            USER_A,
            seller_id,
            product_id,
            course_id,
            offer_id,
            expected_product_version=1,
            expected_course_version=2,
            expected_offer_version=1,
        )
    with pytest.raises(psycopg.errors.UniqueViolation):
        update_draft(
            USER_A,
            seller_id,
            product_id,
            course_id,
            uuid4(),
            expected_product_version=2,
            expected_course_version=2,
            expected_offer_version=0,
        )
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            """
            select product.version, course.draft_version,
                   (select count(*) from offer where seller_organization_id = %s
                     and catalog_product_id = %s)
            from catalog_product product
            join product_course linked
              on linked.seller_organization_id = product.seller_organization_id
             and linked.catalog_product_id = product.id
            join course
              on course.seller_organization_id = linked.seller_organization_id
             and course.id = linked.course_id
            where product.seller_organization_id = %s and product.id = %s
            """,
            (seller_id, product_id, seller_id, product_id),
        ).fetchone() == (2, 2, 1)


def test_distinct_products_of_same_seller_update_concurrently() -> None:
    assert RUNTIME_URL and ADMIN_URL
    seller_id, product_a, course_a = create_graph()
    product_b, course_b = add_product_graph(seller_id)
    offer_a, offer_b = uuid4(), uuid4()

    def execute_update(
        connection: psycopg.Connection[tuple[object, ...]],
        product_id: UUID,
        course_id: UUID,
        offer_id: UUID,
        label: str,
    ) -> tuple[object, ...]:
        apply_context(connection, USER_A, seller_id, "catalog.draft.update")
        row = connection.execute(
            """
            select offer_id, product_version, course_version, offer_version, created
            from app_private.update_product_course_offer_draft(
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, 1, 1, 0, %s, %s, %s
            )
            """,
            (
                USER_A,
                seller_id,
                product_id,
                f"parallel-{product_id.hex[:12]}",
                course_id,
                offer_id,
                f"Produto {label}",
                f"Descrição {label}",
                f"Curso {label}",
                f"Oferta {label}",
                f"Resumo {label}",
                19900,
                "BRL",
                "one_time",
                uuid4(),
                digest(f"parallel-{label}"),
                uuid4(),
            ),
        ).fetchone()
        assert row is not None
        return row

    with psycopg.connect(RUNTIME_URL) as first:
        first_result = execute_update(first, product_a, course_a, offer_a, "A")

        def second_update() -> tuple[object, ...]:
            assert RUNTIME_URL
            with psycopg.connect(RUNTIME_URL) as second:
                result = execute_update(second, product_b, course_b, offer_b, "B")
                second.commit()
                return result

        executor = ThreadPoolExecutor(max_workers=1)
        second = executor.submit(second_update)
        try:
            second_result = second.result(timeout=2)
        finally:
            first.commit()
            executor.shutdown(wait=True)

    assert first_result[1:] == (2, 2, 1, True)
    assert second_result[1:] == (2, 2, 1, True)
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            """
            select count(*) from audit_event
            where seller_organization_id = %s
              and action = 'catalog.product_draft_updated'
              and target_id in (%s, %s)
            """,
            (seller_id, product_a, product_b),
        ).fetchone() == (2,)


def test_concurrent_update_and_publish_retries_are_exactly_once() -> None:
    seller_id, product_id, course_id = create_graph()
    offer_id, update_key = uuid4(), uuid4()
    update_hash = digest("concurrent-update")

    def update_attempt(_: int) -> tuple[UUID, int, int, int, bool]:
        return update_draft(
            USER_A,
            seller_id,
            product_id,
            course_id,
            offer_id,
            key=update_key,
            body_sha256=update_hash,
        )

    with ThreadPoolExecutor(max_workers=12) as executor:
        updates = list(executor.map(update_attempt, range(12)))
    assert len({result[:4] for result in updates}) == 1
    assert [result[4] for result in updates].count(True) == 1
    assert [result[4] for result in updates].count(False) == 11

    publish_key = uuid4()
    publish_hash = digest("concurrent-publish")

    def publish_attempt(_: int) -> tuple[UUID, int, int, str, bool]:
        return publish(
            USER_A,
            seller_id,
            product_id,
            course_id,
            offer_id,
            product_version=2,
            course_version=2,
            offer_version=1,
            key=publish_key,
            body_sha256=publish_hash,
        )

    with ThreadPoolExecutor(max_workers=12) as executor:
        publications = list(executor.map(publish_attempt, range(12)))
    assert len({result[:4] for result in publications}) == 1
    assert [result[4] for result in publications].count(True) == 1
    assert [result[4] for result in publications].count(False) == 11

    publication_id = publications[0][0]
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            """
            select
              (select count(*) from publication where seller_organization_id = %s
                and catalog_product_id = %s),
              (select count(*) from app_private.catalog_mutation_request
                where seller_organization_id = %s and operation = 'product.publish'
                  and idempotency_key = %s),
              (select count(*) from audit_event where seller_organization_id = %s
                and action = 'catalog.product_published' and target_id = %s),
              (select published_snapshot_id from catalog_product
                where seller_organization_id = %s and id = %s)
            """,
            (
                seller_id,
                product_id,
                seller_id,
                publish_key,
                seller_id,
                product_id,
                seller_id,
                product_id,
            ),
        ).fetchone() == (1, 1, 1, publication_id)


def test_snapshot_is_immutable_stable_and_visible_to_private_detail() -> None:
    seller_id, product_id, course_id = create_graph()
    offer_id = uuid4()
    _, product_version, course_version, offer_version, _ = update_draft(
        USER_A, seller_id, product_id, course_id, offer_id
    )
    publication_id, _, published_product_version, source_sha256, _ = publish(
        USER_A,
        seller_id,
        product_id,
        course_id,
        offer_id,
        product_version=product_version,
        course_version=course_version,
        offer_version=offer_version,
    )
    assert RUNTIME_URL and ADMIN_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, seller_id, "catalog.draft.read")
        row = connection.execute(
            """
            select publication.snapshot -> 'product' ->> 'title',
                   publication.snapshot ? 'offer', publication.snapshot ? 'courses',
                   publication.source_sha256
            from catalog_product product
            left join publication
              on publication.seller_organization_id = product.seller_organization_id
             and publication.catalog_product_id = product.id
             and publication.id = product.published_snapshot_id
            where product.id = %s
            """,
            (product_id,),
        ).fetchone()
        assert row == ("Produto Editado", True, True, source_sha256)

    update_draft(
        USER_A,
        seller_id,
        product_id,
        course_id,
        offer_id,
        expected_product_version=published_product_version,
        expected_course_version=course_version,
        expected_offer_version=offer_version,
        product_title="Novo Draft Não Publicado",
    )
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            """
            select snapshot -> 'product' ->> 'title', source_sha256 = encode(
              digest(convert_to(snapshot::text, 'UTF8'), 'sha256'), 'hex'
            )
            from publication where seller_organization_id = %s and id = %s
            """,
            (seller_id, publication_id),
        ).fetchone() == ("Produto Editado", True)
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "update publication set snapshot = '{}'::jsonb where id = %s",
                (publication_id,),
            )


def test_new_key_without_unpublished_changes_creates_no_side_effect() -> None:
    seller_id, product_id, course_id = create_graph()
    offer_id = uuid4()
    _, product_version, course_version, offer_version, _ = update_draft(
        USER_A, seller_id, product_id, course_id, offer_id
    )
    _, _, published_product_version, _, _ = publish(
        USER_A,
        seller_id,
        product_id,
        course_id,
        offer_id,
        product_version=product_version,
        course_version=course_version,
        offer_version=offer_version,
    )

    with pytest.raises(psycopg.DatabaseError, match="no unpublished changes") as error:
        publish(
            USER_A,
            seller_id,
            product_id,
            course_id,
            offer_id,
            product_version=published_product_version,
            course_version=course_version,
            offer_version=offer_version,
            key=uuid4(),
            body_sha256=digest("same-snapshot-new-key"),
        )
    assert error.value.sqlstate == "P4091"

    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute(
            """
            select
              (select count(*) from publication
                where seller_organization_id = %s and catalog_product_id = %s),
              (select count(*) from app_private.catalog_mutation_request
                where seller_organization_id = %s and catalog_product_id = %s
                  and operation = 'product.publish'),
              (select count(*) from audit_event
                where seller_organization_id = %s and target_id = %s
                  and action = 'catalog.product_published')
            """,
            (
                seller_id,
                product_id,
                seller_id,
                product_id,
                seller_id,
                product_id,
            ),
        ).fetchone() == (1, 1, 1)


def test_cross_tenant_pointer_and_publish_are_rejected() -> None:
    seller_a, product_a, course_a = create_graph()
    offer_a = uuid4()
    _, pv, cv, ov, _ = update_draft(USER_A, seller_a, product_a, course_a, offer_a)
    publication_a, _, _, _, _ = publish(
        USER_A,
        seller_a,
        product_a,
        course_a,
        offer_a,
        product_version=pv,
        course_version=cv,
        offer_version=ov,
    )
    seller_b, product_b, _ = create_graph(USER_B)
    assert RUNTIME_URL and ADMIN_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_B, seller_b, "catalog.publication.create")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                """
                select * from app_private.publish_product(
                  %s, %s, %s, %s, 1, 1, %s, %s, %s, %s
                )
                """,
                (
                    USER_B,
                    seller_b,
                    product_a,
                    offer_a,
                    Jsonb({str(course_a): 1}),
                    uuid4(),
                    digest("cross-tenant"),
                    uuid4(),
                ),
            )
    with psycopg.connect(ADMIN_URL) as connection:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                """
                update catalog_product
                set status = 'published', published_snapshot_id = %s
                where seller_organization_id = %s and id = %s
                """,
                (publication_a, seller_b, product_b),
            )


def test_owner_revocation_is_linearized_with_publication() -> None:
    seller_id, product_id, course_id = create_graph()
    offer_id = uuid4()
    _, pv, cv, ov, _ = update_draft(USER_A, seller_id, product_id, course_id, offer_id)
    assert RUNTIME_URL and ADMIN_URL

    def revoke_owner() -> None:
        assert ADMIN_URL
        with psycopg.connect(ADMIN_URL) as connection:
            connection.execute(
                """
                update seller_membership set status = 'revoked',
                  updated_at = clock_timestamp()
                where seller_organization_id = %s and identity_user_id = %s
                """,
                (seller_id, USER_A),
            )
            connection.commit()

    with psycopg.connect(RUNTIME_URL) as publisher:
        apply_context(publisher, USER_A, seller_id, "catalog.publication.create")
        assert publisher.execute(
            """
            select created from app_private.publish_product(
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                USER_A,
                seller_id,
                product_id,
                offer_id,
                pv,
                ov,
                Jsonb({str(course_id): cv}),
                uuid4(),
                digest("revocation-race"),
                uuid4(),
            ),
        ).fetchone() == (True,)
        with ThreadPoolExecutor(max_workers=1) as executor:
            revocation = executor.submit(revoke_owner)
            time.sleep(0.2)
            assert not revocation.done(), "revocation bypassed publication authorization lock"
            publisher.commit()
            revocation.result(timeout=5)
    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        publish(
            USER_A,
            seller_id,
            product_id,
            course_id,
            offer_id,
            product_version=pv + 1,
            course_version=cv,
            offer_version=ov,
        )


def test_seller_suspension_is_linearized_with_publication() -> None:
    seller_id, product_id, course_id = create_graph()
    offer_id = uuid4()
    _, pv, cv, ov, _ = update_draft(USER_A, seller_id, product_id, course_id, offer_id)
    assert RUNTIME_URL and ADMIN_URL

    def suspend_seller() -> None:
        assert ADMIN_URL
        with psycopg.connect(ADMIN_URL) as connection:
            connection.execute(
                """
                update seller_organization set status = 'suspended',
                  updated_at = clock_timestamp()
                where id = %s
                """,
                (seller_id,),
            )
            connection.commit()

    with psycopg.connect(RUNTIME_URL) as publisher:
        apply_context(publisher, USER_A, seller_id, "catalog.publication.create")
        assert publisher.execute(
            """
            select created from app_private.publish_product(
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                USER_A,
                seller_id,
                product_id,
                offer_id,
                pv,
                ov,
                Jsonb({str(course_id): cv}),
                uuid4(),
                digest("suspension-race"),
                uuid4(),
            ),
        ).fetchone() == (True,)
        with ThreadPoolExecutor(max_workers=1) as executor:
            suspension = executor.submit(suspend_seller)
            time.sleep(0.2)
            assert not suspension.done(), "suspension bypassed publication authorization lock"
            publisher.commit()
            suspension.result(timeout=5)
    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        publish(
            USER_A,
            seller_id,
            product_id,
            course_id,
            offer_id,
            product_version=pv + 1,
            course_version=cv,
            offer_version=ov,
        )


@pytest.mark.skipif(not MIGRATOR_URL, reason="migrator URL is required for N-1 upgrade")
def test_upgrade_from_0003_preserves_existing_draft() -> None:
    assert ADMIN_URL and MIGRATOR_URL
    database_name = f"appsell_publication_upgrade_{uuid4().hex}"
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
                "0003_product_course_drafts",
            ],
            cwd=repository_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        seller_id, product_id = uuid4(), uuid4()
        with psycopg.connect(target_url) as connection:
            connection.execute(
                """
                insert into seller_organization (id, slug, display_name, status)
                values (%s, %s, 'Upgrade Seller', 'active')
                """,
                (seller_id, f"upgrade-{uuid4().hex[:12]}"),
            )
            connection.execute(
                """
                insert into catalog_product (seller_organization_id, id, slug, title)
                values (%s, %s, %s, 'Preserved Draft')
                """,
                (seller_id, product_id, f"draft-{uuid4().hex[:12]}"),
            )
            connection.commit()
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "apps/api/alembic.ini", "upgrade", "head"],
            cwd=repository_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        with psycopg.connect(target_url) as connection:
            assert connection.execute(
                """
                select (select version_num from alembic_version), status,
                       published_snapshot_id, to_regclass('public.publication') is not null
                from catalog_product where seller_organization_id = %s and id = %s
                """,
                (seller_id, product_id),
            ).fetchone() == (
                "0032_platform_superadmin_catalog",
                "draft",
                None,
                True,
            )
    finally:
        with psycopg.connect(maintenance_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("drop database if exists {} with (force)").format(
                    sql.Identifier(database_name)
                )
            )


@pytest.mark.skipif(not MIGRATOR_URL, reason="migrator URL is required for downgrade test")
def test_offer_status_migration_preserves_history_and_allows_clean_downgrade() -> None:
    assert ADMIN_URL and RUNTIME_URL and MIGRATOR_URL and CONTEXT_KEY_HEX
    transitioned_database = f"appsell_offer_status_{uuid4().hex}"
    clean_database = f"appsell_offer_status_clean_{uuid4().hex}"
    maintenance_url = make_conninfo(ADMIN_URL, dbname="postgres")
    repository_root = Path(__file__).resolve().parents[3]

    def database_url(base_url: str, database_name: str) -> str:
        return make_conninfo(base_url, dbname=database_name)

    def migrator_url(database_name: str) -> str:
        return (
            make_url(MIGRATOR_URL).set(database=database_name).render_as_string(hide_password=False)
        )

    def create_database(database_name: str) -> None:
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

    def run_migration(
        database_name: str, *arguments: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 -- arguments are fixed by this test
            [sys.executable, "-m", "alembic", "-c", "apps/api/alembic.ini", *arguments],
            cwd=repository_root,
            env={**os.environ, "APPSELL_MIGRATOR_DATABASE_URL": migrator_url(database_name)},
            check=check,
            capture_output=True,
            text=True,
        )

    create_database(transitioned_database)
    create_database(clean_database)
    try:
        run_migration(transitioned_database, "upgrade", "0018_offer_pricing")
        target_admin_url = database_url(ADMIN_URL, transitioned_database)
        target_runtime_url = database_url(RUNTIME_URL, transitioned_database)
        actor, seller_id, product_id, offer_id, publication_id = (
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
        )
        with psycopg.connect(target_admin_url) as connection:
            connection.execute(
                "insert into app_private.context_signing_key (secret) values (%s)",
                (bytes.fromhex(CONTEXT_KEY_HEX),),
            )
            connection.execute(
                """
                insert into identity_user (
                  id, identity_issuer, identity_sub, email_normalized, email_verified
                ) values (%s, 'https://identity.offer-status.test', %s, %s, true)
                """,
                (actor, f"offer-status-{actor.hex}", f"{actor.hex}@example.test"),
            )
            connection.execute(
                """
                insert into seller_organization (id, slug, display_name, status)
                values (%s, %s, 'Offer status migration', 'active')
                """,
                (seller_id, f"offer-status-{seller_id.hex[:12]}"),
            )
            connection.execute(
                """
                insert into seller_membership (
                  seller_organization_id, identity_user_id, role, status
                ) values (%s, %s, 'seller_owner', 'active')
                """,
                (seller_id, actor),
            )
            connection.execute(
                """
                insert into catalog_product (
                  seller_organization_id, id, slug, title, description
                ) values (%s, %s, %s, 'Offer status product', '')
                """,
                (seller_id, product_id, f"offer-status-{product_id.hex[:12]}"),
            )
            connection.execute(
                """
                insert into offer (
                  seller_organization_id, id, catalog_product_id, title, summary,
                  unit_amount_minor, currency, billing_period
                ) values (%s, %s, %s, 'Offer status', '', 100, 'BRL', 'one_time')
                """,
                (seller_id, offer_id, product_id),
            )
            connection.execute(
                """
                insert into publication (
                  seller_organization_id, id, catalog_product_id, version,
                  schema_version, source_sha256, snapshot, actor_identity_user_id
                ) values (%s, %s, %s, 1, 1, %s, '{}'::jsonb, %s)
                """,
                (seller_id, publication_id, product_id, digest("migration-publication"), actor),
            )
            connection.execute(
                """
                update catalog_product
                set status = 'published', published_snapshot_id = %s
                where seller_organization_id = %s and id = %s
                """,
                (publication_id, seller_id, product_id),
            )
            connection.commit()

        run_migration(transitioned_database, "upgrade", "head")
        with psycopg.connect(target_admin_url) as connection:
            assert connection.execute(
                "select status, version from offer where id = %s", (offer_id,)
            ).fetchone() == ("draft", 1)

        with psycopg.connect(target_runtime_url) as connection:
            apply_context(connection, actor, seller_id, "catalog.offer.status.update")
            assert connection.execute(
                """
                select offer_status, offer_version, changed
                from app_private.transition_offer_status(
                  %s, %s, %s, %s, 'draft', 'active', 1, %s, %s, %s
                )
                """,
                (actor, seller_id, product_id, offer_id, uuid4(), digest("activate"), uuid4()),
            ).fetchone() == ("active", 2, True)
            connection.commit()
        with psycopg.connect(target_runtime_url) as connection:
            apply_context(connection, actor, seller_id, "catalog.offer.status.update")
            assert connection.execute(
                """
                select offer_status, offer_version, changed
                from app_private.transition_offer_status(
                  %s, %s, %s, %s, 'active', 'archived', 2, %s, %s, %s
                )
                """,
                (actor, seller_id, product_id, offer_id, uuid4(), digest("archive"), uuid4()),
            ).fetchone() == ("archived", 3, True)
            connection.commit()

        failed_downgrade = run_migration(
            transitioned_database, "downgrade", "0018_offer_pricing", check=False
        )
        assert failed_downgrade.returncode != 0
        assert "active or archived offers exist; status data must be preserved" in (
            failed_downgrade.stdout + failed_downgrade.stderr
        )
        with psycopg.connect(target_admin_url) as connection:
            assert connection.execute(
                """
                select (select version_num from alembic_version), status, version,
                       (select count(*) from app_private.catalog_mutation_request
                        where operation = 'offer.status.update'),
                       to_regprocedure(
                         'app_private.transition_offer_status(uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid)'
                       ) is not null
                from offer where id = %s
                """,
                (offer_id,),
            ).fetchone() == (
                "0032_platform_superadmin_catalog",
                "archived",
                3,
                2,
                True,
            )
            connection.execute("update offer set status = 'draft' where id = %s", (offer_id,))
            connection.commit()

        history_downgrade = run_migration(
            transitioned_database, "downgrade", "0018_offer_pricing", check=False
        )
        assert history_downgrade.returncode != 0
        assert "offer status transition history exists; idempotency data must be preserved" in (
            history_downgrade.stdout + history_downgrade.stderr
        )
        with psycopg.connect(target_admin_url) as connection:
            assert connection.execute(
                """
                select (select version_num from alembic_version), status, version,
                       (select count(*) from app_private.catalog_mutation_request
                        where operation = 'offer.status.update'),
                       to_regprocedure(
                         'app_private.transition_offer_status(uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid)'
                       ) is not null
                from offer where id = %s
                """,
                (offer_id,),
            ).fetchone() == ("0032_platform_superadmin_catalog", "draft", 3, 2, True)

        run_migration(clean_database, "upgrade", "head")
        run_migration(clean_database, "downgrade", "0018_offer_pricing")
        with psycopg.connect(database_url(ADMIN_URL, clean_database)) as connection:
            assert connection.execute(
                """
                select (select version_num from alembic_version),
                       to_regprocedure(
                         'app_private.transition_offer_status(uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid)'
                       ) is null
                """
            ).fetchone() == ("0018_offer_pricing", True)
    finally:
        with psycopg.connect(maintenance_url, autocommit=True) as connection:
            for database_name in (transitioned_database, clean_database):
                connection.execute(
                    sql.SQL("drop database if exists {} with (force)").format(
                        sql.Identifier(database_name)
                    )
                )
