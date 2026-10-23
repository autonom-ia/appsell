"""Produto novo de loja vinculada ao Financial ganha mapping (#465), num Postgres real.

Três lojas no mesmo canal global, como a 0059 deixa:

* `vinculada`, no papel da Hub2You: vínculo Financial, endpoint do canal e um
  produto antigo já mapeado;
* `outra`, também vinculada e no canal, para o isolamento A×B;
* `solta`, com o endpoint do canal mas sem vínculo Financial;
* `sem-canal`, vinculada mas sem o endpoint do canal.

Os produtos novos entram como `appsell_owner`, que passa pela RLS forçada e
pela política `*_owner_all`, como o caminho real das funções do domínio.
"""

from __future__ import annotations

import hashlib
import os
from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType
from uuid import UUID, uuid4

import psycopg
import pytest

ADMIN_URL = os.getenv("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.getenv("APPSELL_TEST_POSTGRES_RUNTIME_URL")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL)),
    reason="isolated PostgreSQL environment is required",
)

RUN = uuid4().hex[:8]
VERSOES = Path(__file__).parents[1] / "migrations" / "versions"

Connection = psycopg.Connection[tuple[object, ...]]


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, label: str, *, bound: bool, on_channel: bool) -> None:
        self.label = label
        self.slug = f"map-{label}-{RUN}"
        self.id = uuid4()
        self.bound = bound
        self.on_channel = on_channel
        self.integration = uuid4()


def _channel_hash(connection: Connection) -> str:
    """O hash do canal pela regra da 0059; cria um canal se o banco não tem."""
    row = connection.execute(
        "select endpoint.endpoint_key_sha256 from webhook_endpoint endpoint "
        "join checkout_integration integration "
        "on integration.seller_organization_id=endpoint.seller_organization_id "
        "and integration.id=endpoint.checkout_integration_id "
        "where endpoint.status='active' and integration.status='active' "
        "order by endpoint.created_at,endpoint.seller_organization_id,endpoint.id limit 1"
    ).fetchone()
    if row is not None:
        return str(row[0])
    platform = Store("canal", bound=False, on_channel=True)
    channel = _sha(f"canal-{RUN}")
    _seed_store(connection, platform, channel)
    return channel


def _seed_store(connection: Connection, store: Store, channel: str) -> None:
    connection.execute(
        "insert into seller_organization(id,slug,display_name,status) values(%s,%s,%s,'active')",
        (store.id, store.slug, f"Map {store.label}"),
    )
    if store.bound:
        connection.execute(
            "update seller_organization set financial_operator_id=%s,"
            "financial_product_catalog_item_id=%s where id=%s",
            (uuid4(), uuid4(), store.id),
        )
    connection.execute(
        "insert into checkout_integration(seller_organization_id,id,adapter,display_name,"
        "status) values(%s,%s,'generic_hmac_v1','Financial checkout','active')",
        (store.id, store.integration),
    )
    connection.execute(
        "insert into webhook_endpoint(seller_organization_id,checkout_integration_id,"
        "endpoint_key_sha256,secret_reference,status) values(%s,%s,%s,%s,'active')",
        (
            store.id,
            store.integration,
            channel if store.on_channel else _sha(f"{store.slug}:proprio"),
            f"map-{store.label}-{RUN}",
        ),
    )


def _product(store: Store, slug: str) -> UUID:
    assert ADMIN_URL
    product = uuid4()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("set role appsell_owner")
        connection.execute(
            "insert into catalog_product(seller_organization_id,id,slug,title,description)"
            " values(%s,%s,%s,'Produto','Produto')",
            (store.id, product, slug),
        )
    return product


def _mappings(store: Store) -> set[tuple[UUID, str, UUID]]:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        rows = connection.execute(
            "select checkout_integration_id,external_product_id_sha256,catalog_product_id "
            "from external_product_mapping where seller_organization_id=%s",
            (store.id,),
        ).fetchall()
    return {(UUID(str(a)), str(b), UUID(str(c))) for a, b, c in rows}


@pytest.fixture(scope="module")
def stores() -> dict[str, Store]:
    assert ADMIN_URL
    created = {
        "vinculada": Store("vinculada", bound=True, on_channel=True),
        "outra": Store("outra", bound=True, on_channel=True),
        "solta": Store("solta", bound=False, on_channel=True),
        "sem-canal": Store("sem-canal", bound=True, on_channel=False),
    }
    with psycopg.connect(ADMIN_URL) as connection:
        channel = _channel_hash(connection)
        for store in created.values():
            _seed_store(connection, store, channel)
        connection.commit()
    return created


@pytest.fixture(scope="module")
def old_product(stores: dict[str, Store]) -> UUID:
    """O produto antigo da loja vinculada, mapeado como a 0059 deixou."""
    return _product(stores["vinculada"], "antigo")


def test_a_new_product_of_a_bound_store_gets_its_mapping(stores: dict[str, Store]) -> None:
    store = stores["vinculada"]
    product = _product(store, "novo")

    assert (store.integration, _sha(str(product)), product) in _mappings(store)


def test_the_existing_mapping_comes_back_identical(
    stores: dict[str, Store], old_product: UUID
) -> None:
    store = stores["vinculada"]
    before = _mappings(store)
    assert (store.integration, _sha(str(old_product)), old_product) in before

    _product(store, "mais-um")
    after = _mappings(store)

    assert before <= after
    assert len(after - before) == 1


def test_a_store_without_financial_binding_gets_nothing(stores: dict[str, Store]) -> None:
    _product(stores["solta"], "curso")
    assert _mappings(stores["solta"]) == set()


def test_a_bound_store_off_the_channel_gets_nothing(stores: dict[str, Store]) -> None:
    _product(stores["sem-canal"], "curso")
    assert _mappings(stores["sem-canal"]) == set()


def test_a_mapping_never_lands_in_another_store(stores: dict[str, Store]) -> None:
    a, b = stores["vinculada"], stores["outra"]
    product = _product(b, "curso-b")

    assert {row[1:] for row in _mappings(b)} >= {(_sha(str(product)), product)}
    assert all(integration == b.integration for integration, _, _ in _mappings(b))
    assert all(catalog != product for _, _, catalog in _mappings(a))


def test_the_runtime_role_still_cannot_read_mappings(stores: dict[str, Store]) -> None:
    assert RUNTIME_URL
    with (
        psycopg.connect(RUNTIME_URL) as connection,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        connection.execute("select 1 from external_product_mapping limit 1")


def test_the_backfill_maps_what_is_missing_once(stores: dict[str, Store]) -> None:
    assert ADMIN_URL
    store = stores["outra"]
    product = _product(store, "sem-mapping")
    with psycopg.connect(ADMIN_URL) as connection:
        # O estado de produção que o #465 descreve: produto sem mapping.
        connection.execute(
            "delete from external_product_mapping where seller_organization_id=%s "
            "and catalog_product_id=%s",
            (store.id, product),
        )
    assert all(catalog != product for _, _, catalog in _mappings(store))
    backfill = _migration_module()._BACKFILL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("set role appsell_owner")
        connection.execute(backfill)
        connection.execute(backfill)
    mapped = [row for row in _mappings(store) if row[2] == product]
    assert mapped == [(store.integration, _sha(str(product)), product)]
    assert _mappings(stores["solta"]) == set()


def test_schema_ready_knows_the_head() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute("select app_private.schema_ready()").fetchone() == (True,)


def _migration_module() -> ModuleType:
    caminho = next(VERSOES.glob("*_mapping_do_produto_novo.py"))
    spec = importlib_util.spec_from_file_location("appsell_mapping_pg", caminho)
    assert spec is not None and spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
