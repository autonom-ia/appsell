from __future__ import annotations

import json
import os
from uuid import uuid4

import psycopg
import pytest

from appsell_api.reset_demo_tenant import load_manifest, reset_demo

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
IDENTITY_ISSUER = "https://identity.test.autonomia.site"

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL)),
    reason="isolated PostgreSQL environment is required",
)


@pytest.fixture(scope="module", autouse=True)
def seed_storefront() -> None:
    assert ADMIN_URL
    manifest, manifest_sha = load_manifest()
    with psycopg.connect(ADMIN_URL) as connection:
        reset_demo(connection, manifest, manifest_sha, IDENTITY_ISSUER)
        connection.commit()


def test_runtime_can_read_only_published_public_projection() -> None:
    assert RUNTIME_URL
    manifest, _ = load_manifest()
    with psycopg.connect(RUNTIME_URL) as connection:
        projection = connection.execute(
            "select app_private.read_public_storefront(%s)",
            (manifest["seller"]["slug"],),
        ).fetchone()
        privileges = connection.execute(
            """
            select
              has_function_privilege(
                'appsell_runtime','app_private.read_public_storefront(text)','EXECUTE'
              ),
              has_table_privilege('appsell_runtime','catalog_product','SELECT'),
              has_table_privilege('appsell_runtime','publication','SELECT')
            """
        ).fetchone()
        direct_rows = connection.execute(
            """
            select
              (select count(*) from catalog_product),
              (select count(*) from publication)
            """
        ).fetchone()
    assert projection is not None
    payload = projection[0]
    # 0052: o currículo publicado entra na vitrine, só com títulos e contagens.
    curriculum = payload["products"][0].pop("curriculum")
    assert curriculum["lesson_count"] == len(manifest["lessons"])
    assert len(curriculum["lessons"]) == curriculum["lesson_count"]
    assert "content" not in json.dumps(curriculum)
    assert payload == {
        "schema_version": 1,
        "seller": {
            "id": manifest["seller"]["id"],
            "slug": manifest["seller"]["slug"],
            "name": manifest["seller"]["display_name"],
        },
        "products": [
            {
                "id": manifest["product"]["id"],
                "slug": manifest["product"]["slug"],
                "title": manifest["product"]["title"],
                "description": manifest["product"]["description"],
                "publication_id": manifest["publication"]["id"],
                "publication_version": 1,
                "access_mode": "free",
                "price": None,
            }
        ],
    }
    assert privileges == (True, True, True)
    assert direct_rows == (0, 0)
    serialized = json.dumps(payload)
    assert "email" not in serialized and "offer" not in serialized and "buyer" not in serialized


def test_storefront_exposes_live_offer_price_for_published_products() -> None:
    assert ADMIN_URL
    manifest, _ = load_manifest()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            update offer set status = 'active', unit_amount_minor = 49900, currency = 'BRL',
              billing_period = 'monthly'
            where seller_organization_id = %s and catalog_product_id = %s
            """,
            (manifest["seller"]["id"], manifest["product"]["id"]),
        )
        projection = connection.execute(
            "select app_private.read_public_storefront(%s)",
            (manifest["seller"]["slug"],),
        ).fetchone()
        connection.rollback()
    assert projection is not None
    products = projection[0]["products"]
    assert [product["access_mode"] for product in products] == ["paid"]
    assert [product["price"] for product in products] == [
        {"unit_amount_minor": 49900, "currency": "BRL", "billing_period": "monthly"}
    ]


def test_storefront_hides_drafts_and_unknown_or_invalid_sellers() -> None:
    assert ADMIN_URL
    manifest, _ = load_manifest()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            insert into catalog_product(
              seller_organization_id,id,slug,title,description,status
            ) values(%s,%s,'rascunho-invisivel','Rascunho invisível','Não pode vazar.','draft')
            """,
            (manifest["seller"]["id"], uuid4()),
        )
        visible_row = connection.execute(
            "select app_private.read_public_storefront(%s)",
            (manifest["seller"]["slug"],),
        ).fetchone()
        unknown = connection.execute(
            "select app_private.read_public_storefront('seller-inexistente')"
        ).fetchone()
        invalid = connection.execute(
            "select app_private.read_public_storefront('../admin')"
        ).fetchone()
        connection.rollback()
    assert visible_row is not None
    visible = visible_row[0]
    assert [product["id"] for product in visible["products"]] == [manifest["product"]["id"]]
    assert unknown == (None,)
    assert invalid == (None,)


def test_storefront_hides_archived_offers() -> None:
    # Retiring an offer (status='archived') must remove its product from the
    # public storefront, even while the product itself stays published.
    assert ADMIN_URL
    manifest, _ = load_manifest()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            """
            update offer set status = 'archived'
            where seller_organization_id = %s and catalog_product_id = %s
            """,
            (manifest["seller"]["id"], manifest["product"]["id"]),
        )
        projection = connection.execute(
            "select app_private.read_public_storefront(%s)",
            (manifest["seller"]["slug"],),
        ).fetchone()
        connection.rollback()
    assert projection is not None
    product_ids = [product["id"] for product in projection[0]["products"]]
    assert manifest["product"]["id"] not in product_ids
