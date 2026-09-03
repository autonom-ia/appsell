"""#232 — o contrato de capa no banco: quem pode, o que grava, e o que recusa."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

USER_A = UUID("71000000-0000-4000-8000-000000000001")
USER_B = UUID("71000000-0000-4000-8000-000000000002")
SELLER_A = UUID("72000000-0000-4000-8000-000000000001")
SELLER_B = UUID("72000000-0000-4000-8000-000000000002")
PRODUCT_A = UUID("73000000-0000-4000-8000-000000000001")
PRODUCT_B = UUID("73000000-0000-4000-8000-000000000002")
COURSE_A = UUID("74000000-0000-4000-8000-000000000001")
COURSE_B = UUID("74000000-0000-4000-8000-000000000002")
BUYER = UUID("71000000-0000-4000-8000-000000000003")

QUARANTINE = "appsell-quarantine"
READY = "appsell-ready"


def sign_context(
    actor: UUID, seller: UUID | None, capability: str, issued: int, expires: int
) -> str:
    payload = f"{actor}:{seller or ''}:{capability}:{issued}:{expires}"
    return hmac.new(
        bytes.fromhex(CONTEXT_KEY_HEX or ""), payload.encode(), hashlib.sha256
    ).hexdigest()


def apply_context(
    connection: psycopg.Connection[tuple[object, ...]], actor: UUID, seller: UUID, capability: str
) -> None:
    issued = int(time.time())
    expires = issued + 60
    connection.execute(
        "select app_private.apply_request_context(%s,%s,%s,%s,%s,%s)",
        (
            actor,
            seller,
            capability,
            issued,
            expires,
            sign_context(actor, seller, capability, issued, expires),
        ),
    )


def digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


@pytest.fixture(scope="module", autouse=True)
def seed_cover_tenants() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            insert into identity_user(
              id,identity_issuer,identity_sub,email_normalized,email_verified
            )
            values (%s,'https://identity.cover.test','cover-a','cover-a@example.test',true),
                   (%s,'https://identity.cover.test','cover-b','cover-b@example.test',true),
                   (%s,'https://identity.cover.test','cover-buyer','buyer@example.test',true)
            """,
            (USER_A, USER_B, BUYER),
        )
        connection.execute(
            """
            insert into seller_organization(id,slug,display_name,status)
            values (%s,'cover-a','Cover A','active'),(%s,'cover-b','Cover B','active')
            """,
            (SELLER_A, SELLER_B),
        )
        connection.execute(
            """
            insert into seller_membership(seller_organization_id,identity_user_id,role,status)
            values (%s,%s,'seller_owner','active'),(%s,%s,'seller_owner','active')
            """,
            (SELLER_A, USER_A, SELLER_B, USER_B),
        )
        connection.execute(
            """
            insert into catalog_product(seller_organization_id,id,slug,title,description,status)
            values (%s,%s,'cover-product-a','Product A','Description A','draft'),
                   (%s,%s,'cover-product-b','Product B','Description B','draft')
            """,
            (SELLER_A, PRODUCT_A, SELLER_B, PRODUCT_B),
        )
        connection.execute(
            """
            insert into course(seller_organization_id,id,title,description,status,draft_version)
            values (%s,%s,'Course A','Course A','draft',1),(%s,%s,'Course B','Course B','draft',1)
            """,
            (SELLER_A, COURSE_A, SELLER_B, COURSE_B),
        )
        connection.execute(
            """
            insert into product_course(seller_organization_id,catalog_product_id,course_id,position)
            values (%s,%s,%s,0),(%s,%s,%s,0)
            """,
            (SELLER_A, PRODUCT_A, COURSE_A, SELLER_B, PRODUCT_B, COURSE_B),
        )
        connection.commit()


def product_version(product: UUID, seller: UUID) -> int:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select version from catalog_product where seller_organization_id=%s and id=%s",
            (seller, product),
        ).fetchone()
    assert row is not None
    return int(row[0])


def create_intent(
    *,
    actor: UUID = USER_A,
    seller: UUID = SELLER_A,
    product: UUID = PRODUCT_A,
    media_type: str = "image/png",
    byte_size: int = 4096,
    sha: str | None = None,
    kind: str = "cover",
) -> dict[str, Any]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, actor, seller, "catalog.draft.update")
        row = connection.execute(
            "select app_private.create_product_image_upload_intent("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                actor,
                seller,
                product,
                kind,
                media_type,
                byte_size,
                sha or digest("cover"),
                QUARANTINE,
                READY,
                uuid4(),
                digest("body"),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
    assert row is not None
    return cast(dict[str, Any], row[0])


def set_cover(
    intent: dict[str, Any],
    *,
    expected_version: int,
    actor: UUID = USER_A,
    seller: UUID = SELLER_A,
    product: UUID = PRODUCT_A,
    object_key: str | None = None,
    media_type: str = "image/png",
    width: int = 320,
    height: int = 180,
    kind: str = "cover",
) -> tuple[Any, ...]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, actor, seller, "catalog.draft.update")
        row = connection.execute(
            "select * from app_private.set_product_image("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                actor,
                seller,
                product,
                kind,
                UUID(intent["upload_id"]),
                object_key or intent["ready_key"],
                "storage-version-1",
                media_type,
                digest("sanitised"),
                width,
                height,
                4096,
                expected_version,
                uuid4(),
                digest("body"),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
    assert row is not None
    return row


def test_the_intent_reserves_a_key_that_belongs_to_the_product() -> None:
    intent = create_intent()

    assert intent["quarantine_key"] == f"tenants/{SELLER_A}/quarantine/cover/{PRODUCT_A}"
    assert intent["ready_key"].startswith(f"tenants/{SELLER_A}/covers/{PRODUCT_A}/")
    assert intent["ready_key"].endswith(intent["upload_id"])
    assert intent["quarantine_bucket"] == QUARANTINE


def test_a_second_attempt_supersedes_the_first_instead_of_queueing() -> None:
    # O vendedor que fechou o seletor de arquivo nao pode ficar travado ate a
    # tentativa anterior expirar. E o que torna a chave de quarentena por
    # produto segura: nunca ha duas tentativas abertas disputando o mesmo lugar.
    first = create_intent()
    second = create_intent()

    assert first["upload_id"] != second["upload_id"]
    assert first["quarantine_key"] == second["quarantine_key"]
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        rows = connection.execute(
            "select id,status,failure_code from app_private.product_cover_upload"
            " where seller_organization_id=%s and catalog_product_id=%s",
            (SELLER_A, PRODUCT_A),
        ).fetchall()
    states = {str(row[0]): (row[1], row[2]) for row in rows}
    assert states[first["upload_id"]] == ("cancelled", "superseded")
    assert states[second["upload_id"]] == ("pending", None)


def test_only_one_pending_attempt_per_product_can_exist() -> None:
    create_intent()
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        pending = connection.execute(
            "select count(*) from app_private.product_cover_upload"
            " where seller_organization_id=%s and catalog_product_id=%s and status='pending'",
            (SELLER_A, PRODUCT_A),
        ).fetchone()
    assert pending == (1,)


def test_setting_the_cover_records_the_sanitised_file_and_bumps_the_version() -> None:
    intent = create_intent()
    before = product_version(PRODUCT_A, SELLER_A)

    row = set_cover(intent, expected_version=before)

    assert row[0] == before + 1
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        stored = connection.execute(
            "select cover_object_key,cover_storage_version_id,cover_media_type,cover_sha256,"
            "cover_width,cover_height,cover_byte_size,cover_updated_at"
            " from catalog_product where seller_organization_id=%s and id=%s",
            (SELLER_A, PRODUCT_A),
        ).fetchone()
        upload_status = connection.execute(
            "select status from app_private.product_cover_upload where id=%s",
            (UUID(intent["upload_id"]),),
        ).fetchone()
    assert stored is not None
    assert stored[0] == intent["ready_key"]
    assert stored[1] == "storage-version-1"
    assert stored[3] == digest("sanitised")
    assert (stored[4], stored[5]) == (320, 180)
    assert all(value is not None for value in stored)
    assert upload_status == ("ready",)


def test_the_cover_can_only_point_at_the_key_the_intent_reserved() -> None:
    # Sem esta checagem, quem falasse com a funcao apontaria a capa para
    # qualquer objeto do bucket servido — inclusive de outro vendedor.
    intent = create_intent()
    version = product_version(PRODUCT_A, SELLER_A)

    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        set_cover(
            intent,
            expected_version=version,
            object_key=f"tenants/{SELLER_B}/covers/{PRODUCT_B}/{uuid4()}",
        )


def test_the_media_type_cannot_change_between_intent_and_write() -> None:
    intent = create_intent(media_type="image/png")
    version = product_version(PRODUCT_A, SELLER_A)

    with pytest.raises(psycopg.errors.InvalidParameterValue):
        set_cover(intent, expected_version=version, media_type="image/webp")


def test_a_closed_attempt_cannot_become_a_cover() -> None:
    intent = create_intent()
    create_intent()  # supersedes the first
    version = product_version(PRODUCT_A, SELLER_A)

    with pytest.raises(psycopg.Error) as failure:
        set_cover(intent, expected_version=version)
    # P4229 e o codigo proprio de "esta tentativa ja foi fechada". Conferir o
    # codigo, e nao a classe do driver, porque codigos da classe P nao tem
    # excecao dedicada em psycopg.
    assert failure.value.sqlstate == "P4229"


def test_a_stale_product_version_is_refused() -> None:
    intent = create_intent()
    version = product_version(PRODUCT_A, SELLER_A)

    with pytest.raises(psycopg.errors.SerializationFailure):
        set_cover(intent, expected_version=version + 5)


def test_another_seller_cannot_touch_this_products_cover() -> None:
    intent = create_intent()
    version = product_version(PRODUCT_A, SELLER_A)

    # O contexto e do vendedor B, mas os argumentos falam do produto de A. A
    # resposta e "nao encontrado", e nao "nao autorizado", porque a busca e
    # escopada ao vendedor B: para ele, aquele upload realmente nao existe.
    # Responder 28000 confirmaria que o produto do outro existe, que e
    # exatamente o que a resposta nao pode dizer.
    with pytest.raises(psycopg.Error) as recusa:
        set_cover(intent, expected_version=version, actor=USER_B, seller=SELLER_B)
    assert recusa.value.sqlstate == "P4041"


def test_a_read_capability_cannot_write_a_cover() -> None:
    intent = create_intent()
    version = product_version(PRODUCT_A, SELLER_A)
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_A, "catalog.draft.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select * from app_private.set_product_image("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    USER_A,
                    SELLER_A,
                    PRODUCT_A,
                    "cover",
                    UUID(intent["upload_id"]),
                    intent["ready_key"],
                    "storage-version-1",
                    "image/png",
                    digest("sanitised"),
                    320,
                    180,
                    4096,
                    version,
                    uuid4(),
                    digest("body"),
                    uuid4(),
                ),
            )


def test_clearing_twice_reaches_the_same_place_as_clearing_once() -> None:
    intent = create_intent()
    set_cover(intent, expected_version=product_version(PRODUCT_A, SELLER_A))
    version = product_version(PRODUCT_A, SELLER_A)

    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_A, "catalog.draft.update")
        first = connection.execute(
            "select * from app_private.clear_product_image(%s,%s,%s,%s,%s,%s,%s,%s)",
            (USER_A, SELLER_A, PRODUCT_A, "cover", version, uuid4(), digest("body"), uuid4()),
        ).fetchone()
        connection.commit()
    assert first is not None
    assert first[1] is True
    assert first[2] == intent["ready_key"]

    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_A, "catalog.draft.update")
        second = connection.execute(
            "select * from app_private.clear_product_image(%s,%s,%s,%s,%s,%s,%s,%s)",
            (USER_A, SELLER_A, PRODUCT_A, "cover", first[0], uuid4(), digest("body"), uuid4()),
        ).fetchone()
        connection.commit()
    assert second is not None
    assert second[1] is False
    assert second[0] == first[0]


def test_a_half_written_cover_is_impossible() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "update catalog_product set cover_object_key=%s"
                " where seller_organization_id=%s and id=%s",
                ("tenants/x/covers/y/z", SELLER_A, PRODUCT_A),
            )
        connection.rollback()


def test_the_student_count_reaches_enrolment_without_widening_the_role() -> None:
    # learning_enrollment nao e legivel pelo papel de runtime, e nao pode
    # passar a ser. A funcao atravessa a fronteira e devolve so numeros.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_A, SELLER_A, "catalog.draft.read")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("select count(*) from learning_enrollment")
        connection.rollback()
        apply_context(connection, USER_A, SELLER_A, "catalog.draft.read")
        rows = connection.execute(
            "select * from app_private.product_student_counts(%s,%s)", (USER_A, SELLER_A)
        ).fetchall()
    assert all(len(row) == 2 for row in rows)


def test_the_student_count_refuses_a_context_from_another_seller() -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, USER_B, SELLER_B, "catalog.draft.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select * from app_private.product_student_counts(%s,%s)", (USER_A, SELLER_A)
            )
