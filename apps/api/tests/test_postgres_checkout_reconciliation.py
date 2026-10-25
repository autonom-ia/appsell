"""A leitura de conciliação num Postgres de verdade (#147, migration 0063).

Duas lojas, cada uma com um caso de cada categoria e com casos limpos. O teste
prova que cada divergência aparece uma vez, na loja certa, e que o caso limpo
não aparece. Os dados entram pelo superusuário, mas a leitura sai pelo papel
`appsell_reconciler`, que é o caminho real: o que se testa é a leitura, não o
caminho que produziu os dados (esse está em test_postgres_checkout_fulfillment.py).

Os instantes são explícitos, porque os dois erros que a revisão achou
(`dead_letter` e job parado filtrados pela criação) só aparecem com dados
criados fora da janela.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from appsell_api.webhook_reconciliation import read_divergences

ADMIN_URL = os.getenv("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.getenv("APPSELL_TEST_POSTGRES_RUNTIME_URL")
RECONCILER_URL = os.getenv("APPSELL_TEST_POSTGRES_RECONCILER_URL")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, RECONCILER_URL)),
    reason="isolated PostgreSQL environment is required",
)

RUN = uuid4().hex[:8]
ISSUER = "https://identity.test.autonomia.site"
NOW = datetime.now(UTC)
SINCE = NOW - timedelta(days=2)
BEFORE = NOW + timedelta(minutes=5)
STALE = timedelta(minutes=30)
# Antes de qualquer janela que a conciliação aceite (31 dias).
LONG_AGO = NOW - timedelta(days=40)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, label: str) -> None:
        self.label = label
        self.slug = f"recon-{label}-{RUN}"
        self.seller = uuid4()
        self.integration = uuid4()
        self.endpoint = uuid4()
        self.product = uuid4()
        self.publication = uuid4()
        self.expected: dict[str, UUID] = {}
        self.late_event: UUID | None = None

    def purchase(self, name: str) -> str:
        return _sha(f"{self.slug}:{name}")


Connection = psycopg.Connection[tuple[object, ...]]


def _buyer(connection: Connection, name: str) -> UUID:
    buyer = uuid4()
    connection.execute(
        "insert into identity_user(id,identity_issuer,identity_sub,email_normalized,"
        "email_verified) values(%s,%s,%s,%s,true)",
        (buyer, ISSUER, f"recon-{name}-{RUN}", f"recon.{name}.{RUN}@example.test"),
    )
    return buyer


def _event(
    connection: Connection,
    store: Store,
    name: str,
    event_type: str,
    received_at: datetime = NOW,
) -> UUID:
    event = uuid4()
    connection.execute(
        "insert into webhook_event(seller_organization_id,id,checkout_integration_id,"
        "webhook_endpoint_id,external_event_id_sha256,external_event_id_octets,"
        "payload_sha256,payload_octets,event_type,external_product_id_sha256,"
        "external_product_id_octets,external_purchase_id_sha256,external_purchase_id_octets,"
        "occurred_at,signed_at,received_at) "
        "values(%s,%s,%s,%s,%s,10,%s,100,%s,%s,10,%s,10,%s,%s,%s)",
        (
            store.seller,
            event,
            store.integration,
            store.endpoint,
            _sha(f"{store.slug}:event:{name}"),
            _sha(f"{store.slug}:payload:{name}"),
            event_type,
            _sha(f"{store.slug}:product"),
            store.purchase(name),
            received_at,
            received_at,
            received_at,
        ),
    )
    return event


def _job(
    connection: Connection,
    store: Store,
    name: str,
    status: str,
    created_at: datetime,
    error_code: str | None = None,
) -> UUID:
    event = _event(connection, store, name, "purchase.approved", created_at)
    job = connection.execute(
        "insert into app_private.fulfillment_job(seller_organization_id,webhook_event_id,"
        "event_type,external_product_id_sha256,external_purchase_id_sha256,"
        "buyer_email_normalized,status,error_code,created_at,updated_at) "
        "values(%s,%s,'purchase.approved',%s,%s,%s,%s,%s,%s,%s) returning id",
        (
            store.seller,
            event,
            _sha(f"{store.slug}:product"),
            store.purchase(name),
            f"recon.{name}@example.test",
            status,
            error_code,
            created_at,
            created_at,
        ),
    ).fetchone()
    assert job is not None
    return cast(UUID, job[0])


def _grant(
    connection: Connection,
    store: Store,
    name: str,
    buyer: UUID,
    status: str,
) -> None:
    connection.execute(
        "insert into app_private.entitlement_grant(seller_organization_id,"
        "checkout_integration_id,external_purchase_id_sha256,buyer_identity_user_id,"
        "catalog_product_id,status,occurred_at) values(%s,%s,%s,%s,%s,%s,now())",
        (store.seller, store.integration, store.purchase(name), buyer, store.product, status),
    )


def _access(
    connection: Connection,
    store: Store,
    buyer: UUID,
    status: str,
    source: str = "billing",
) -> UUID:
    access = uuid4()
    connection.execute(
        "insert into buyer_entitlement(seller_organization_id,id,buyer_identity_user_id,"
        "catalog_product_id,publication_id,reference_key,source,status)"
        " values(%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            store.seller,
            access,
            buyer,
            store.product,
            store.publication,
            f"recon:{access.hex}",
            source,
            status,
        ),
    )
    return access


def _seed_store(connection: Connection, store: Store) -> None:
    connection.execute(
        "insert into seller_organization(id,slug,display_name,status) values(%s,%s,%s,'active')",
        (store.seller, store.slug, f"Recon {store.label}"),
    )
    connection.execute(
        "insert into catalog_product(seller_organization_id,id,slug,title,description,status)"
        " values(%s,%s,'curso','Curso','Curso','draft')",
        (store.seller, store.product),
    )
    connection.execute(
        "insert into publication(seller_organization_id,id,catalog_product_id,version,"
        "schema_version,source_sha256,snapshot,actor_identity_user_id) "
        "values(%s,%s,%s,1,2,repeat('0',64),%s,%s)",
        (
            store.seller,
            store.publication,
            store.product,
            Jsonb({}),
            _buyer(connection, f"{store.label}-owner"),
        ),
    )
    connection.execute(
        "insert into checkout_integration(seller_organization_id,id,adapter,display_name,"
        "status) values(%s,%s,'generic_hmac_v1','Recon checkout','active')",
        (store.seller, store.integration),
    )
    connection.execute(
        "insert into webhook_endpoint(seller_organization_id,id,checkout_integration_id,"
        "endpoint_key_sha256,secret_reference,status) values(%s,%s,%s,%s,%s,'active')",
        (
            store.seller,
            store.endpoint,
            store.integration,
            _sha(store.slug),
            f"recon-{store.label}-{RUN}",
        ),
    )


def _seed(connection: Connection, store: Store) -> None:
    _seed_store(connection, store)
    expected = store.expected

    # Estado: aparece mesmo criado fora de qualquer janela (ALTO 1 da revisão).
    expected["fulfillment_dead_letter"] = _job(
        connection, store, "dead", "dead_letter", LONG_AGO, "product_mapping_missing"
    )
    # Parado há mais que o limite, criado antes da janela (ALTO 2 da revisão).
    expected["fulfillment_stalled"] = _job(
        connection, store, "stalled", "retry", LONG_AGO + timedelta(days=1)
    )
    expected["fulfillment_completed_without_grant"] = _job(
        connection, store, "done-no-grant", "completed", NOW - timedelta(hours=1)
    )
    lost = _buyer(connection, f"{store.label}-lost")
    _grant(connection, store, "lost", lost, "active")
    expected["grant_without_access"] = _access(connection, store, lost, "revoked")
    stray = _buyer(connection, f"{store.label}-stray")
    expected["access_without_grant"] = _access(connection, store, stray, "active")
    kept = _buyer(connection, f"{store.label}-kept")
    _grant(connection, store, "kept", kept, "refunded")
    expected["refunded_with_access"] = _access(connection, store, kept, "active")
    # Evento: dentro da janela pelo received_at.
    expected["approved_without_fulfillment"] = _event(
        connection, store, "orphan", "purchase.approved", NOW - timedelta(hours=1)
    )
    expected["refund_without_fulfillment"] = _event(
        connection, store, "orphan-refund", "purchase.refunded", NOW - timedelta(hours=1)
    )
    # Evento sem job fora da janela: só a execução que cobre o instante o vê.
    store.late_event = _event(
        connection, store, "orphan-old", "subscription.cancelled", SINCE - timedelta(hours=1)
    )

    # Casos limpos, que não podem aparecer.
    happy = _buyer(connection, f"{store.label}-happy")
    _job(connection, store, "happy", "completed", NOW - timedelta(hours=1))
    _grant(connection, store, "happy", happy, "active")
    _access(connection, store, happy, "active")
    # Pendente há menos que o limite: ainda não está parado.
    _job(connection, store, "fresh", "pending", NOW - STALE + timedelta(minutes=2))
    refunded = _buyer(connection, f"{store.label}-refunded")
    _grant(connection, store, "refunded", refunded, "refunded")
    _access(connection, store, refunded, "revoked")
    # Estorno de uma compra, outra compra ativa do mesmo produto: o acesso fica.
    twice = _buyer(connection, f"{store.label}-twice")
    _grant(connection, store, "twice-old", twice, "refunded")
    _grant(connection, store, "twice-new", twice, "active")
    _access(connection, store, twice, "active")
    # Acesso dado à mão não é venda: fora do `access_without_grant`.
    _access(connection, store, _buyer(connection, f"{store.label}-manual"), "active", "manual")


@pytest.fixture(scope="module")
def stores() -> list[Store]:
    assert ADMIN_URL
    created = [Store("a"), Store("b")]
    with psycopg.connect(ADMIN_URL) as connection:
        for store in created:
            _seed(connection, store)
        connection.commit()
    return created


def _report(
    stores: list[Store],
    *,
    since: datetime = SINCE,
    before: datetime = BEFORE,
) -> list[dict[str, object]]:
    assert RECONCILER_URL
    slugs = {store.slug for store in stores}
    rows = read_divergences(RECONCILER_URL, since=since, before=before, stale_after=STALE)
    return [row for row in rows if row["seller_slug"] in slugs]


def _found(rows: list[dict[str, object]]) -> list[tuple[str, str, str]]:
    return sorted(
        (str(row["seller_slug"]), str(row["category"]), str(row["reference_id"])) for row in rows
    )


def test_reports_each_divergence_once_under_its_own_store(stores: list[Store]) -> None:
    expected = sorted(
        (store.slug, category, str(reference))
        for store in stores
        for category, reference in store.expected.items()
    )
    assert _found(_report(stores)) == expected


def test_state_categories_ignore_the_window(stores: list[Store]) -> None:
    # Uma janela no passado, sem nenhum evento: o que é estado continua lá.
    rows = _report(stores, since=LONG_AGO - timedelta(days=5), before=LONG_AGO - timedelta(days=4))
    assert {str(row["category"]) for row in rows} == {
        "fulfillment_dead_letter",
        "fulfillment_stalled",
        "fulfillment_completed_without_grant",
        "grant_without_access",
        "access_without_grant",
        "refunded_with_access",
    }


def test_consecutive_windows_see_every_event_exactly_once(stores: list[Store]) -> None:
    # Execuções encostadas: [SINCE-1d, SINCE) e [SINCE, BEFORE). O evento de
    # SINCE-1h cai só na primeira, e os de NOW-1h só na segunda.
    earlier = _report(stores, since=SINCE - timedelta(days=1), before=SINCE)
    later = _report(stores)

    def events(rows: list[dict[str, object]]) -> list[str]:
        return sorted(
            str(row["reference_id"]) for row in rows if row["reference_type"] == "webhook_event"
        )

    assert events(earlier) == sorted(str(store.late_event) for store in stores)
    assert set(events(earlier)).isdisjoint(events(later))
    assert len(events(later)) == 2 * len(stores)


def test_rows_carry_no_buyer_data(stores: list[Store]) -> None:
    for row in _report(stores):
        assert "example.test" not in " ".join(str(value) for value in row.values())
        for store in stores:
            if row["seller_slug"] == store.slug:
                assert row["seller_organization_id"] == store.seller


def test_dead_letter_keeps_its_error_code(stores: list[Store]) -> None:
    dead = [row for row in _report(stores) if row["category"] == "fulfillment_dead_letter"]
    assert {row["error_code"] for row in dead} == {"product_mapping_missing"}


def test_running_twice_gives_the_same_report_and_writes_nothing(stores: list[Store]) -> None:
    assert ADMIN_URL
    counts = (
        "select (select count(*) from app_private.fulfillment_job),"
        "(select count(*) from app_private.entitlement_grant),"
        "(select count(*) from buyer_entitlement),"
        "(select max(updated_at) from buyer_entitlement)"
    )
    with psycopg.connect(ADMIN_URL) as connection:
        before = connection.execute(counts).fetchone()
    first = _report(stores)
    second = _report(stores)
    with psycopg.connect(ADMIN_URL) as connection:
        after = connection.execute(counts).fetchone()
    assert first == second
    assert before == after


@pytest.mark.parametrize(
    ("since", "before", "stale"),
    [
        (BEFORE, SINCE, STALE),
        (SINCE - timedelta(days=40), BEFORE, STALE),
        (SINCE, BEFORE, timedelta(seconds=10)),
        (SINCE, BEFORE, timedelta(days=8)),
    ],
)
def test_refuses_an_invalid_window(
    stores: list[Store], since: datetime, before: datetime, stale: timedelta
) -> None:
    assert RECONCILER_URL
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        read_divergences(RECONCILER_URL, since=since, before=before, stale_after=stale)


def test_the_runtime_role_cannot_run_it(stores: list[Store]) -> None:
    assert RUNTIME_URL
    with (
        psycopg.connect(RUNTIME_URL) as connection,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        connection.execute(
            "select * from app_private.checkout_reconciliation_report(%s,%s,%s)",
            (SINCE, BEFORE, STALE),
        )


@pytest.mark.parametrize(
    ("schema", "table"), [("app_private", "fulfillment_job"), ("public", "buyer_entitlement")]
)
def test_the_reconciler_role_reads_nothing_but_the_report(
    stores: list[Store], schema: str, table: str
) -> None:
    assert RECONCILER_URL
    with (
        psycopg.connect(RECONCILER_URL) as connection,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        connection.execute(
            sql.SQL("select 1 from {} limit 1").format(sql.Identifier(schema, table))
        )


def test_the_reconciler_role_starts_read_only(stores: list[Store]) -> None:
    assert RECONCILER_URL
    with psycopg.connect(RECONCILER_URL) as connection:
        assert connection.execute("show default_transaction_read_only").fetchone() == ("on",)


def test_schema_ready_knows_the_head() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        ready = connection.execute("select app_private.schema_ready()").fetchone()
    assert ready == (True,)
