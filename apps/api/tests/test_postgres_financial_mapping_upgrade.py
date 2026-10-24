"""A 0062 sobre o estado que a 0059 deixou em produção (#465).

Num banco dedicado, a cadeia sobe até a versão anterior à 0062, recebe o estado
legado e só então sobe até a cabeça. Assim o mapping legado existe **antes** da
migration, como em produção, e o teste prova que ele sai intacto.

Estado legado semeado:

* `canal`, a loja dona do canal: endpoint do `provision_checkout`
  ("Checkout externo") com o hash do canal e um mapping por chave externa;
* `hub`, no papel da Hub2You: vinculada, com a integração "Financial checkout"
  que a 0059 grava, um produto mapeado e outro sem mapping;
* `antiga`, com um endpoint antigo desativado, de outro hash.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Iterator
from importlib import util as importlib_util
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from sqlalchemy.engine import make_url

import appsell_api.reset_demo_tenant as reset_module

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
MIGRATOR_URL = os.environ.get("APPSELL_MIGRATOR_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, MIGRATOR_URL)),
    reason="isolated PostgreSQL migrator environment is required",
)

VERSOES = Path(__file__).parents[1] / "migrations" / "versions"
CHANNEL = hashlib.sha256(b"canal-legado").hexdigest()
OLD_KEY = hashlib.sha256(b"endpoint-antigo").hexdigest()

Connection = psycopg.Connection[tuple[Any, ...]]


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _revision_before_0062() -> str:
    """A versão de antes da 0062, lida da própria migration: sem literal de versão."""
    caminho = next(VERSOES.glob("*_mapping_do_produto_novo.py"))
    spec = importlib_util.spec_from_file_location("appsell_mapping_upgrade", caminho)
    assert spec is not None and spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.down_revision)


def _alembic(migrator_url: str, target: str) -> None:
    # O alvo vem de arquivo de migration do repo ou é "head", nunca de entrada externa.
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "-c", "apps/api/alembic.ini", "upgrade", target],
        cwd=Path(__file__).resolve().parents[3],
        env={**os.environ, "APPSELL_MIGRATOR_DATABASE_URL": migrator_url},
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def database() -> Iterator[tuple[str, str]]:
    assert ADMIN_URL and MIGRATOR_URL
    name = f"appsell_mapping_upgrade_{uuid4().hex}"
    maintenance = make_conninfo(ADMIN_URL, dbname="postgres")
    migrator_role = make_url(MIGRATOR_URL).username
    assert migrator_role
    with psycopg.connect(maintenance, autocommit=True) as connection:
        connection.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
        for statement in (
            "alter database {} owner to appsell_owner",
            "revoke connect on database {} from public",
        ):
            connection.execute(sql.SQL(statement).format(sql.Identifier(name)))
        connection.execute(
            sql.SQL("comment on database {} is {}").format(
                sql.Identifier(name), sql.Literal(reset_module.DEMO_DATABASE_MARKER)
            )
        )
        connection.execute(
            sql.SQL("grant connect on database {} to {},appsell_runtime").format(
                sql.Identifier(name), sql.Identifier(migrator_role)
            )
        )
    migrator_url = make_url(MIGRATOR_URL).set(database=name).render_as_string(hide_password=False)
    try:
        yield make_conninfo(ADMIN_URL, dbname=name), migrator_url
    finally:
        with psycopg.connect(maintenance, autocommit=True) as connection:
            connection.execute(
                sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(name))
            )


def _seller(connection: Connection, slug: str, *, bound: bool) -> UUID:
    row = connection.execute(
        "insert into seller_organization(slug,display_name,status) values(%s,%s,'active') "
        "returning id",
        (slug, slug),
    ).fetchone()
    assert row is not None
    seller = UUID(str(row[0]))
    if bound:
        connection.execute(
            "update seller_organization set financial_operator_id=%s,"
            "financial_product_catalog_item_id=%s where id=%s",
            (uuid4(), uuid4(), seller),
        )
    return seller


def _integration(
    connection: Connection,
    seller: UUID,
    *,
    name: str,
    key: str,
    status: str = "active",
    created_at: str | None = None,
) -> UUID:
    integration = uuid4()
    connection.execute(
        "insert into checkout_integration(seller_organization_id,id,adapter,display_name,"
        "status,created_at) values(%s,%s,'generic_hmac_v1',%s,'active',"
        "coalesce(%s::timestamptz,clock_timestamp()))",
        (seller, integration, name, created_at),
    )
    connection.execute(
        "insert into webhook_endpoint(seller_organization_id,checkout_integration_id,"
        "endpoint_key_sha256,secret_reference,status,created_at) "
        "values(%s,%s,%s,%s,%s,coalesce(%s::timestamptz,clock_timestamp()))",
        (
            seller,
            integration,
            key,
            "financial-canal" if key == CHANNEL else f"ref-{key[:8]}",
            status,
            created_at,
        ),
    )
    return integration


def _product(connection: Connection, seller: UUID, slug: str) -> UUID:
    product = uuid4()
    connection.execute(
        "insert into catalog_product(seller_organization_id,id,slug,title,description)"
        " values(%s,%s,%s,'Produto','Produto')",
        (seller, product, slug),
    )
    return product


def _mapping(
    connection: Connection, seller: UUID, integration: UUID, external: str, product: UUID
) -> None:
    connection.execute(
        "insert into external_product_mapping(seller_organization_id,checkout_integration_id,"
        "external_product_id_sha256,external_product_id_octets,catalog_product_id) "
        "values(%s,%s,%s,%s,%s)",
        (seller, integration, _sha(external), len(external.encode()), product),
    )


def _all_mappings(connection: Connection) -> list[tuple[Any, ...]]:
    return connection.execute(
        "select seller_organization_id,checkout_integration_id,external_product_id_sha256,"
        "external_product_id_octets,catalog_product_id,created_at "
        "from external_product_mapping order by created_at,catalog_product_id"
    ).fetchall()


@pytest.fixture(scope="module")
def legacy(database: tuple[str, str]) -> dict[str, Any]:
    admin, migrator = database
    _alembic(migrator, _revision_before_0062())
    with psycopg.connect(admin) as connection:
        canal = _seller(connection, "canal-legado", bound=False)
        canal_integration = _integration(
            connection, canal, name="Checkout externo", key=CHANNEL, created_at="2026-01-01"
        )
        canal_product = _product(connection, canal, "teste-checkout")
        _mapping(connection, canal, canal_integration, "financial-produto-legado", canal_product)

        hub = _seller(connection, "hub-legado", bound=True)
        hub_integration = _integration(connection, hub, name="Financial checkout", key=CHANNEL)
        mapped = _product(connection, hub, "condicoes-gerais")
        _mapping(connection, hub, hub_integration, str(mapped), mapped)
        unmapped = _product(connection, hub, "produto-depois-da-0059")

        antiga = _seller(connection, "loja-antiga", bound=False)
        _integration(
            connection,
            antiga,
            name="Checkout externo",
            key=OLD_KEY,
            status="disabled",
            created_at="2000-01-01",
        )
        connection.commit()
        before = _all_mappings(connection)
    _alembic(migrator, "head")
    return {
        "admin": admin,
        "before": before,
        "hub": hub,
        "hub_integration": hub_integration,
        "unmapped": unmapped,
        "antiga": antiga,
    }


def test_the_legacy_mappings_come_back_intact(legacy: dict[str, Any]) -> None:
    # Pega o upgrade reescrevendo ou apagando mapping que já vende.
    with psycopg.connect(legacy["admin"]) as connection:
        after = _all_mappings(connection)
    assert after[: len(legacy["before"])] == legacy["before"]
    assert set(after) >= set(legacy["before"])


def test_the_backfill_maps_the_product_created_after_0059(legacy: dict[str, Any]) -> None:
    # O estado do #465: produto da Hub2You sem mapping.
    with psycopg.connect(legacy["admin"]) as connection:
        added = [row for row in _all_mappings(connection) if row not in legacy["before"]]
    product = legacy["unmapped"]
    assert [row[:5] for row in added] == [
        (legacy["hub"], legacy["hub_integration"], _sha(str(product)), 36, product)
    ]


def test_reactivating_the_old_endpoint_does_not_move_the_channel(
    legacy: dict[str, Any],
) -> None:
    # Pega a heurística do "mais antigo": o endpoint de 2000 reativado viraria o
    # canal e o produto novo da Hub2You ficaria sem mapping.
    with psycopg.connect(legacy["admin"]) as connection:
        connection.execute(
            "update webhook_endpoint set status='active' where seller_organization_id=%s",
            (legacy["antiga"],),
        )
        connection.execute("set role appsell_owner")
        product = _product(connection, legacy["hub"], "produto-novo")
        connection.execute("reset role")
        row = connection.execute(
            "select checkout_integration_id from external_product_mapping "
            "where catalog_product_id=%s",
            (product,),
        ).fetchall()
        connection.rollback()
    assert row == [(legacy["hub_integration"],)]
