"""A oferta padrão no banco de verdade, fase 1 da #553.

O que este arquivo prova, sobre o seed da demo:

- toda oferta existente é a padrão, e oferta nova também nasce padrão;
- a fase 1 ainda recusa a segunda oferta do mesmo produto;
- simulando a fase 2 dentro de uma transação desfeita (sem a unique antiga),
  o banco recusa duas padrões no mesmo produto, e a vitrine continua com um
  card só, com o preço da padrão, mesmo quando outra oferta está ativa.

Tudo que muda o banco é desfeito com rollback.
"""

from __future__ import annotations

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


def _ids() -> tuple[str, str, str]:
    manifest, _ = load_manifest()
    return manifest["seller"]["id"], manifest["product"]["id"], manifest["seller"]["slug"]


def _inserir_oferta(
    connection: psycopg.Connection, seller: str, product: str, *, is_default: bool | None
) -> None:
    """Oferta extra ativa (R$ 123,00). `None` deixa o banco usar o default da coluna."""
    if is_default is None:
        connection.execute(
            "insert into offer(seller_organization_id,id,catalog_product_id,title,status,"
            "unit_amount_minor) values(%s,%s,%s,'Oferta extra','active',12300)",
            (seller, uuid4(), product),
        )
        return
    connection.execute(
        "insert into offer(seller_organization_id,id,catalog_product_id,title,status,"
        "unit_amount_minor,is_default) values(%s,%s,%s,'Oferta extra','active',12300,%s)",
        (seller, uuid4(), product, is_default),
    )


def test_a_oferta_da_demo_e_a_padrao_e_a_coluna_nasce_verdadeira() -> None:
    assert ADMIN_URL
    seller, product, _ = _ids()
    with psycopg.connect(ADMIN_URL) as connection:
        padroes = connection.execute(
            "select is_default from offer "
            "where seller_organization_id=%s and catalog_product_id=%s",
            (seller, product),
        ).fetchall()
        coluna = connection.execute(
            """
            select is_nullable, column_default from information_schema.columns
            where table_schema='public' and table_name='offer' and column_name='is_default'
            """
        ).fetchone()
        indice = connection.execute(
            "select indexdef from pg_indexes where schemaname='public' "
            "and indexname='offer_one_default_per_product'"
        ).fetchone()
    assert padroes == [(True,)]
    assert coluna == ("NO", "true")
    assert indice is not None
    assert "UNIQUE" in indice[0] and "WHERE is_default" in indice[0]


def test_oferta_de_produto_criado_depois_da_migration_nasce_padrao() -> None:
    # O insert do editor e os seeds não informam is_default: o default da
    # coluna tem de fazer da primeira oferta a padrão, senão o produto some
    # da vitrine.
    assert ADMIN_URL
    seller, _, _ = _ids()
    novo = uuid4()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into catalog_product(seller_organization_id,id,slug,title,description,status)"
            " values(%s,%s,'produto-depois-da-0067','Produto novo','Depois da 0067.','draft')",
            (seller, novo),
        )
        _inserir_oferta(connection, seller, str(novo), is_default=None)
        linha = connection.execute(
            "select is_default from offer "
            "where seller_organization_id=%s and catalog_product_id=%s",
            (seller, novo),
        ).fetchone()
        connection.rollback()
    assert linha == (True,)


def test_sem_a_unique_antiga_o_banco_recusa_duas_padroes() -> None:
    assert ADMIN_URL
    seller, product, _ = _ids()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("alter table offer drop constraint if exists offer_seller_product_key")
        with pytest.raises(psycopg.errors.UniqueViolation) as recusa:
            _inserir_oferta(connection, seller, product, is_default=None)
        connection.rollback()
    assert recusa.value.diag.constraint_name == "offer_one_default_per_product"


def test_a_vitrine_mostra_so_a_padrao_mesmo_com_outra_ativa() -> None:
    assert ADMIN_URL
    seller, product, slug = _ids()
    with psycopg.connect(ADMIN_URL) as connection:
        antes = connection.execute(
            "select app_private.read_public_storefront(%s)", (slug,)
        ).fetchone()
        connection.execute("alter table offer drop constraint if exists offer_seller_product_key")
        _inserir_oferta(connection, seller, product, is_default=False)
        depois = connection.execute(
            "select app_private.read_public_storefront(%s)", (slug,)
        ).fetchone()
        connection.rollback()
    assert antes is not None and depois is not None
    assert [p["id"] for p in depois[0]["products"]] == [product]
    # A oferta extra (ativa, R$ 123,00) não muda nada do que a vitrine mostra.
    assert depois[0]["products"] == antes[0]["products"]
