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


def _channel(connection: Connection) -> tuple[str, str]:
    """O canal pela identidade que a 0059 grava; cria um canal se o banco não tem."""
    row = connection.execute(
        "select distinct endpoint.endpoint_key_sha256,endpoint.secret_reference "
        "from webhook_endpoint endpoint "
        "join checkout_integration integration "
        "on integration.seller_organization_id=endpoint.seller_organization_id "
        "and integration.id=endpoint.checkout_integration_id "
        "where integration.adapter='generic_hmac_v1' "
        "and integration.display_name='Financial checkout' "
        "and endpoint.status='active' and integration.status='active'"
    ).fetchall()
    assert len(row) <= 1, "o banco de teste tem mais de um canal Financial"
    if row:
        return str(row[0][0]), str(row[0][1])
    platform = Store("canal", bound=False, on_channel=True)
    channel = (_sha(f"canal-{RUN}"), f"map-canal-{RUN}")
    _seed_store(connection, platform, channel)
    return channel


def _seed_store(connection: Connection, store: Store, channel: tuple[str, str]) -> None:
    """Endpoint do canal copia hash e secret_reference do canal, como a 0059."""
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
    # Só o endpoint do canal leva o nome que a 0059 grava; o endpoint próprio de
    # uma loja fora do canal é um "Checkout externo", como o provision_checkout.
    connection.execute(
        "insert into checkout_integration(seller_organization_id,id,adapter,display_name,"
        "status) values(%s,%s,'generic_hmac_v1',%s,'active')",
        (
            store.id,
            store.integration,
            "Financial checkout" if store.on_channel else "Checkout externo",
        ),
    )
    connection.execute(
        "insert into webhook_endpoint(seller_organization_id,checkout_integration_id,"
        "endpoint_key_sha256,secret_reference,status) values(%s,%s,%s,%s,'active')",
        (
            (store.id, store.integration, *channel)
            if store.on_channel
            else (store.id, store.integration, _sha(f"{store.slug}:proprio"), store.slug)
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
        channel = _channel(connection)
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


def test_an_old_endpoint_reactivated_does_not_move_the_channel(
    stores: dict[str, Store],
) -> None:
    # O defeito que a Qualidade provou: com "o endpoint ativo mais antigo", um
    # endpoint antigo de outro adapter reativado virava o canal, e o produto novo
    # ficava sem mapping em silêncio.
    assert ADMIN_URL
    old = Store("antigo", bound=False, on_channel=False)
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into seller_organization(id,slug,display_name,status) "
            "values(%s,%s,'Map antigo','active')",
            (old.id, old.slug),
        )
        connection.execute(
            "insert into checkout_integration(seller_organization_id,id,adapter,display_name,"
            "status,created_at) values(%s,%s,'generic_hmac_v1','Checkout externo','active',"
            "'2000-01-01')",
            (old.id, old.integration),
        )
        connection.execute(
            "insert into webhook_endpoint(seller_organization_id,checkout_integration_id,"
            "endpoint_key_sha256,secret_reference,status,created_at) "
            "values(%s,%s,%s,%s,'disabled','2000-01-01')",
            (old.id, old.integration, _sha(f"antigo-{RUN}"), f"map-antigo-{RUN}"),
        )
        connection.execute(
            "update webhook_endpoint set status='active' where seller_organization_id=%s",
            (old.id,),
        )
        connection.commit()
    store = stores["vinculada"]
    product = _product(store, "depois-do-antigo")

    assert (store.integration, _sha(str(product)), product) in _mappings(store)


def test_an_ambiguous_channel_never_blocks_product_creation(stores: dict[str, Store]) -> None:
    # Dois canais "Financial checkout" ativos: o produto entra, sem mapping, e o
    # trigger avisa. Bloquear a criação seria regressão para toda loja vinculada,
    # a Hub2You inclusive. Tudo numa transação desfeita: o banco é compartilhado.
    assert ADMIN_URL
    intruder = Store("intruso", bound=True, on_channel=True)
    store = stores["vinculada"]
    product = uuid4()
    warnings: list[str] = []
    with psycopg.connect(ADMIN_URL) as connection:
        connection.add_notice_handler(
            lambda diagnostic: warnings.append(str(diagnostic.message_primary))
        )
        _seed_store(connection, intruder, (_sha(f"intruso-{RUN}"), f"intruso-{RUN}"))
        connection.execute("set role appsell_owner")
        connection.execute(
            "insert into catalog_product(seller_organization_id,id,slug,title,description)"
            " values(%s,%s,'ambiguo','Produto','Produto')",
            (store.id, product),
        )
        mapped = connection.execute(
            "select count(*) from external_product_mapping where catalog_product_id=%s",
            (product,),
        ).fetchone()
        connection.rollback()
    assert mapped == (0,)
    assert any(
        message.startswith("appsell.financial_channel_unresolved") and str(product) in message
        for message in warnings
    )


def _channel_ready_as_runtime(connection: psycopg.Connection[tuple[object, ...]]) -> object:
    connection.execute("set role appsell_runtime")
    row = connection.execute("select app_private.financial_channel_ready()").fetchone()
    connection.execute("reset role")
    assert row is not None
    return row[0]


def test_the_channel_readiness_follows_the_bound_stores(stores: dict[str, Store]) -> None:
    # O que a API pergunta no startup e no ready. Pega três erros: o runtime sem
    # grant (a sonda viraria "desconhecido" para sempre), o canal ambíguo dado
    # como pronto, e banco sem loja vinculada (dev, demo) reprovando o startup.
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        assert _channel_ready_as_runtime(connection) is True
        _seed_store(
            connection,
            Store("intruso-ready", bound=True, on_channel=True),
            (_sha(f"intruso-ready-{RUN}"), f"intruso-ready-{RUN}"),
        )
        assert _channel_ready_as_runtime(connection) is False
        connection.execute(
            "update seller_organization set financial_operator_id=null,"
            "financial_product_catalog_item_id=null"
        )
        assert _channel_ready_as_runtime(connection) is True
        connection.rollback()


# O SQL do runbook de rotação do segredo do canal (corpo do PR #466): troca todas
# as cópias numa transação, para o canal nunca ter duas identidades.
CHANNEL_ROTATION_SQL = (
    "update public.webhook_endpoint endpoint "
    "set secret_reference=%s, revision=endpoint.revision+1, updated_at=clock_timestamp() "
    "from app_private.financial_channel() canal "
    "where endpoint.endpoint_key_sha256=canal.endpoint_key_sha256 "
    "and endpoint.secret_reference=canal.secret_reference"
)


def test_the_secret_rotation_runbook_keeps_one_channel(stores: dict[str, Store]) -> None:
    # Pega a rotação parcial: trocar a referência em só uma cópia deixaria duas
    # identidades e o produto novo sem mapping.
    assert ADMIN_URL
    store = stores["outra"]
    product = uuid4()
    new_reference = f"rotacao-{RUN}"
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("set role appsell_owner")
        rotated = connection.execute(CHANNEL_ROTATION_SQL, (new_reference,)).rowcount
        channel = connection.execute(
            "select secret_reference from app_private.financial_channel()"
        ).fetchall()
        connection.execute(
            "insert into catalog_product(seller_organization_id,id,slug,title,description)"
            " values(%s,%s,'depois-da-rotacao','Produto','Produto')",
            (store.id, product),
        )
        mapped = connection.execute(
            "select checkout_integration_id from external_product_mapping "
            "where catalog_product_id=%s",
            (product,),
        ).fetchall()
        connection.rollback()
    assert rotated >= 3
    assert channel == [(new_reference,)]
    assert mapped == [(store.integration,)]


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
