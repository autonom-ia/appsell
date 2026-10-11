from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg.types.json import Jsonb

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)

ACTOR = UUID("91000000-0000-4000-8000-000000000001")
SELLER_A = UUID("92000000-0000-4000-8000-000000000001")
SELLER_B = UUID("92000000-0000-4000-8000-000000000002")
SELLER_SUSPENDED = UUID("92000000-0000-4000-8000-000000000003")
PRODUCT_A1 = UUID("93000000-0000-4000-8000-000000000001")
PRODUCT_A2 = UUID("93000000-0000-4000-8000-000000000002")
PRODUCT_A_DRAFT = UUID("93000000-0000-4000-8000-000000000003")
PRODUCT_A_UNPUBLISHED = UUID("93000000-0000-4000-8000-000000000004")
PRODUCT_B = UUID("93000000-0000-4000-8000-000000000005")
PRODUCT_SUSPENDED = UUID("93000000-0000-4000-8000-000000000006")
PRODUCT_MALFORMED = UUID("93000000-0000-4000-8000-000000000007")
SELLER_LARGE = UUID("92000000-0000-4000-8000-000000000010")
PRODUCT_LARGE = UUID("93000000-0000-4000-8000-000000000010")
PUBLICATION_LARGE = UUID("94000000-0000-4000-8000-000000000010")
SELLER_INVALID = UUID("92000000-0000-4000-8000-000000000011")


def apply_context(
    connection: psycopg.Connection[Any],
    capability: str,
    seller: UUID | None = None,
) -> None:
    issued = int(time.time())
    expires = issued + 60
    payload = f"{ACTOR}:{seller or ''}:{capability}:{issued}:{expires}"
    signature = hmac.new(
        bytes.fromhex(CONTEXT_KEY_HEX or ""), payload.encode(), hashlib.sha256
    ).hexdigest()
    connection.execute(
        "select app_private.apply_request_context(%s,%s,%s,%s,%s,%s)",
        (ACTOR, seller, capability, issued, expires, signature),
    )


@pytest.fixture(scope="module", autouse=True)
def seed_public_storefront_graph() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into identity_user("
            "id,identity_issuer,identity_sub,email_normalized,email_verified) "
            "values(%s,'https://identity.storefront.test','storefront-owner',"
            "'storefront.owner@autonomia.site',true)",
            (ACTOR,),
        )
        with connection.cursor() as cursor:
            cursor.executemany(
                "insert into seller_organization(id,slug,display_name,status) values(%s,%s,%s,%s)",
                [
                    (SELLER_A, "storefront-alpha", "Produtor Alpha", "active"),
                    (SELLER_B, "storefront-beta", "Produtor Beta", "active"),
                    (
                        SELLER_SUSPENDED,
                        "storefront-suspenso",
                        "Produtor Suspenso",
                        "suspended",
                    ),
                ],
            )
        insert_product(connection, SELLER_A, PRODUCT_A1, "alpha-curso", "Curso Alpha", 3)
        insert_product(connection, SELLER_A, PRODUCT_A2, "alpha-segundo", "Segundo Alpha", 2)
        insert_product(connection, SELLER_B, PRODUCT_B, "beta-curso", "Curso Beta", 1)
        insert_product(
            connection,
            SELLER_SUSPENDED,
            PRODUCT_SUSPENDED,
            "suspenso-curso",
            "Curso Suspenso",
            3,
        )
        connection.execute(
            "insert into catalog_product(seller_organization_id,id,slug,title,description,status) "
            "values(%s,%s,'alpha-rascunho','Rascunho Alpha','Privado','draft')",
            (SELLER_A, PRODUCT_A_DRAFT),
        )
        insert_product(
            connection,
            SELLER_A,
            PRODUCT_A_UNPUBLISHED,
            "alpha-retirado",
            "Produto Retirado",
            3,
            status="unpublished",
        )
        insert_product(
            connection,
            SELLER_A,
            PRODUCT_MALFORMED,
            "alpha-malformado",
            "Produto Malformado",
            3,
            status="unpublished",
            malformed=True,
        )
        connection.commit()


def insert_product(
    connection: psycopg.Connection[Any],
    seller: UUID,
    product: UUID,
    slug: str,
    title: str,
    schema_version: int,
    *,
    status: str = "published",
    malformed: bool = False,
) -> None:
    publication = UUID(str(product).replace("93000000", "94000000"))
    course = UUID(str(product).replace("93000000", "96000000"))
    snapshot = {
        "schema_version": schema_version,
        "seller_organization_id": str(seller),
        "product": {
            "id": str(product),
            "slug": slug,
            "title": 42 if malformed else title,
            "description": f"Descrição pública de {title}",
        },
        "offer": {
            "id": "95000000-0000-4000-8000-000000000001",
            "title": f"Acesso {title}",
            "summary": "Resumo público",
            "billing_authority": "external_unconfigured",
        },
        "courses": [
            {
                "id": str(course),
                "title": f"Conteúdo de {title}",
                "description": "Visão pública do curso",
                "position": 0,
                "modules": [],
                "content": "SEGREDO-DA-AULA-NUNCA-PUBLICAR",
                "assets": [{"file_name": "privado.pdf", "sha256": "ab" * 32}],
            }
        ],
    }
    connection.execute(
        "insert into catalog_product(seller_organization_id,id,slug,title,description,status) "
        "values(%s,%s,%s,%s,%s,'draft')",
        (seller, product, slug, title, f"Draft privado de {title}"),
    )
    connection.execute(
        "insert into course(seller_organization_id,id,title,description,status) "
        "values(%s,%s,%s,'Conteúdo privado','draft')",
        (seller, course, f"Curso de {title}"),
    )
    connection.execute(
        "insert into product_course(seller_organization_id,catalog_product_id,course_id,position) "
        "values(%s,%s,%s,0)",
        (seller, product, course),
    )
    connection.execute(
        "insert into publication(seller_organization_id,id,catalog_product_id,version,"
        "schema_version,source_sha256,snapshot,actor_identity_user_id) "
        "values(%s,%s,%s,1,%s,%s,%s,%s)",
        (
            seller,
            publication,
            product,
            schema_version,
            hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest(),
            Jsonb(snapshot),
            ACTOR,
        ),
    )
    connection.execute(
        "update catalog_product set status=%s,published_snapshot_id=%s "
        "where seller_organization_id=%s and id=%s",
        (status, publication, seller, product),
    )


def read_storefront(seller_slug: str, cursor: str | None = None, limit: int = 24) -> Any:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, "storefront.public.read")
        row = connection.execute(
            "select app_private.read_public_storefront(%s,%s,%s)",
            (seller_slug, cursor, limit),
        ).fetchone()
        assert row is not None
        return row[0]


def read_product(seller_slug: str, product_slug: str) -> Any:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, "storefront.public.read")
        row = connection.execute(
            "select app_private.read_public_product(%s,%s)",
            (seller_slug, product_slug),
        ).fetchone()
        assert row is not None
        return row[0]


def test_public_projection_is_allowlisted_paginated_and_seller_scoped() -> None:
    first = read_storefront("storefront-alpha", limit=1)
    assert first["seller"] == {"slug": "storefront-alpha", "name": "Produtor Alpha"}
    assert [product["slug"] for product in first["products"]] == ["alpha-curso"]
    assert first["next_cursor"] == "alpha-curso"
    second = read_storefront("storefront-alpha", first["next_cursor"], 1)
    assert [product["slug"] for product in second["products"]] == ["alpha-segundo"]
    assert second["next_cursor"] is None

    serialized = json.dumps([first, second], sort_keys=True)
    for forbidden in (
        str(SELLER_A),
        str(PRODUCT_A1),
        "SEGREDO-DA-AULA-NUNCA-PUBLICAR",
        "privado.pdf",
        "sha256",
        "modules",
        "lessons",
        "assets",
        "buyer",
        "publication_version",
    ):
        assert forbidden not in serialized
    assert "beta-curso" not in serialized
    assert "alpha-rascunho" not in serialized
    assert "alpha-retirado" not in serialized
    assert "alpha-malformado" not in serialized


def test_detail_and_other_products_never_cross_tenant() -> None:
    detail = read_product("storefront-alpha", "alpha-curso")
    assert detail["seller"] == {"slug": "storefront-alpha", "name": "Produtor Alpha"}
    assert detail["product"]["slug"] == "alpha-curso"
    assert detail["product"]["courses"] == [
        {
            "title": "Conteúdo de Curso Alpha",
            "description": "Visão pública do curso",
            "position": 0,
        }
    ]
    assert [product["slug"] for product in detail["other_products"]] == ["alpha-segundo"]
    assert read_product("storefront-beta", "alpha-curso") is None
    assert read_product("storefront-alpha", "beta-curso") is None
    v1_detail = read_product("storefront-beta", "beta-curso")
    assert v1_detail["product"]["title"] == "Curso Beta"
    v1_serialized = json.dumps(v1_detail, sort_keys=True)
    for forbidden in (str(SELLER_B), str(PRODUCT_B), "content", "assets", "modules"):
        assert forbidden not in v1_serialized


def test_draft_edits_do_not_change_the_persisted_public_projection() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update catalog_product set title='Título privado ainda não publicado',"
            "description='Descrição privada ainda não publicada' "
            "where seller_organization_id=%s and id=%s",
            (SELLER_A, PRODUCT_A1),
        )
        connection.commit()
    detail = read_product("storefront-alpha", "alpha-curso")
    assert detail["product"]["title"] == "Curso Alpha"
    assert "ainda não publicado" not in json.dumps(detail, ensure_ascii=False)


def test_projection_supports_one_hundred_maximum_size_public_courses() -> None:
    assert ADMIN_URL
    courses = [
        {
            "title": "T" * 160,
            "description": "\\" * 2_000,
            "position": position,
        }
        for position in range(100)
    ]
    assert len(json.dumps(courses).encode()) > 262_144
    snapshot = public_snapshot(SELLER_LARGE, PRODUCT_LARGE, "catalogo-grande", courses)
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into seller_organization(id,slug,display_name,status) "
            "values(%s,'storefront-grande','Produtor Grande','active')",
            (SELLER_LARGE,),
        )
        insert_raw_publication(
            connection,
            SELLER_LARGE,
            PRODUCT_LARGE,
            PUBLICATION_LARGE,
            "catalogo-grande",
            snapshot,
        )
        connection.commit()
    detail = read_product("storefront-grande", "catalogo-grande")
    assert len(detail["product"]["courses"]) == 100
    assert detail["product"]["courses"][-1]["position"] == 99


def test_malformed_legacy_snapshots_are_published_but_never_projected() -> None:
    assert ADMIN_URL
    variants: list[dict[str, Any]] = [
        {"courses": {}},
        {
            "courses": [
                {"title": "Posição inválida", "description": "Privado", "position": 9_999_999_999}
            ]
        },
        {"courses": [{"title": "Curso inválido", "description": "x" * 2_001, "position": 0}]},
        {"product_title": "Título\ncom controle"},
        {"product_description": {"secret": "private"}},
        {"product_title": 42},
        {"product_slug": 123},
        {"offer_summary": {"secret": "private"}},
        {"product_id": "93000000-0000-4000-8000-999999999999"},
    ]
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into seller_organization(id,slug,display_name,status) "
            "values(%s,'storefront-invalido','Produtor Inválido','active')",
            (SELLER_INVALID,),
        )
        for index, override in enumerate(variants, start=1):
            product = UUID(f"93000000-0000-4000-8000-{index:012d}")
            publication = UUID(f"94000000-0000-4000-8000-{index:012d}")
            slug = f"invalido-{index}"
            snapshot = public_snapshot(
                SELLER_INVALID,
                product,
                slug,
                [{"title": "Curso válido", "description": "Público", "position": 0}],
            )
            if "courses" in override:
                snapshot["courses"] = override["courses"]
            if "product_title" in override:
                snapshot["product"]["title"] = override["product_title"]
            if "product_description" in override:
                snapshot["product"]["description"] = override["product_description"]
            if "product_slug" in override:
                snapshot["product"]["slug"] = override["product_slug"]
            if "offer_summary" in override:
                snapshot["offer"]["summary"] = override["offer_summary"]
            if "product_id" in override:
                snapshot["product"]["id"] = override["product_id"]
            insert_raw_publication(connection, SELLER_INVALID, product, publication, slug, snapshot)
        connection.commit()
    assert read_storefront("storefront-invalido") is None


def test_non_public_states_and_invalid_inputs_fail_closed() -> None:
    assert read_storefront("storefront-suspenso") is None
    assert read_product("storefront-alpha", "alpha-rascunho") is None
    assert read_product("storefront-alpha", "alpha-retirado") is None
    assert read_product("storefront-alpha", "alpha-malformado") is None
    assert read_storefront("inexistente-storefront") is None
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, "storefront.public.read")
        with pytest.raises(psycopg.Error) as invalid:
            connection.execute(
                "select app_private.read_public_storefront('INVALID',null,24)"
            ).fetchone()
        assert invalid.value.sqlstate == "22023"


def test_capability_and_null_seller_context_are_mandatory() -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, "library.read")
        with pytest.raises(psycopg.Error) as wrong_capability:
            connection.execute(
                "select app_private.read_public_storefront('storefront-alpha',null,24)"
            ).fetchone()
        assert wrong_capability.value.sqlstate == "28000"
        connection.rollback()
        apply_context(connection, "storefront.public.read", SELLER_A)
        with pytest.raises(psycopg.Error) as seller_bound:
            connection.execute(
                "select app_private.read_public_storefront('storefront-alpha',null,24)"
            ).fetchone()
        assert seller_bound.value.sqlstate == "28000"


def test_security_definer_privileges_and_search_path_are_minimal() -> None:
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        assert connection.execute("select app_private.schema_ready()").fetchone() == (True,)
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            """
            select
              (select version_num from alembic_version),
              has_function_privilege(
                'appsell_runtime','app_private.read_public_storefront(text,text,integer)','EXECUTE'
              ),
              has_function_privilege(
                'appsell_runtime','app_private.read_public_product(text,text)','EXECUTE'
              ),
              (select proconfig from pg_proc
                where oid='app_private.read_public_storefront(text,text,integer)'::regprocedure),
              has_table_privilege(
                'appsell_runtime','storefront_product_projection','SELECT'
              ),
              has_table_privilege(
                'appsell_runtime','storefront_product_projection','INSERT'
              ),
              (select relrowsecurity and relforcerowsecurity from pg_class
                where oid='storefront_product_projection'::regclass),
              (select count(*) from information_schema.routine_privileges
                where routine_schema='app_private'
                  and routine_name in ('read_public_storefront','read_public_product')
                  and grantee='PUBLIC')
            """
        ).fetchone()
    assert row == (
        "0009_public_storefront",
        True,
        True,
        ["search_path=pg_catalog, app_private"],
        False,
        False,
        True,
        0,
    )


def public_snapshot(
    seller: UUID,
    product: UUID,
    slug: str,
    courses: Any,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "seller_organization_id": str(seller),
        "product": {
            "id": str(product),
            "slug": slug,
            "title": "Produto público",
            "description": "Descrição pública",
        },
        "offer": {"title": "Acesso público", "summary": "Resumo público"},
        "courses": courses,
    }


def insert_raw_publication(
    connection: psycopg.Connection[Any],
    seller: UUID,
    product: UUID,
    publication: UUID,
    slug: str,
    snapshot: dict[str, Any],
) -> None:
    connection.execute(
        "insert into catalog_product(seller_organization_id,id,slug,title,description,status) "
        "values(%s,%s,%s,'Produto de teste','Privado','draft')",
        (seller, product, slug),
    )
    serialized = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    connection.execute(
        "insert into publication(seller_organization_id,id,catalog_product_id,version,"
        "schema_version,source_sha256,snapshot,actor_identity_user_id) "
        "values(%s,%s,%s,1,1,%s,%s::jsonb,%s)",
        (
            seller,
            publication,
            product,
            hashlib.sha256(serialized.encode()).hexdigest(),
            serialized,
            ACTOR,
        ),
    )
    connection.execute(
        "update catalog_product set status='published',published_snapshot_id=%s "
        "where seller_organization_id=%s and id=%s",
        (publication, seller, product),
    )
