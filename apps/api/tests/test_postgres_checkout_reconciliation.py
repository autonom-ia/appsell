"""A leitura de conciliação num Postgres de verdade (#147, migration 0062).

Duas lojas, cada uma com um caso de cada categoria e um caso limpo. O teste
prova que cada divergência aparece uma vez, na loja certa, e que o caso limpo
não aparece. Os dados entram direto pelo superusuário: o que se testa é a
leitura, não o caminho que os produziu (esse está em
test_postgres_checkout_fulfillment.py).
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from appsell_api.webhook_reconciliation import read_divergences

ADMIN_URL = os.getenv("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.getenv("APPSELL_TEST_POSTGRES_RUNTIME_URL")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL)), reason="isolated PostgreSQL environment is required"
)

RUN = uuid4().hex[:8]
ISSUER = "https://identity.test.autonomia.site"
SINCE = datetime.now(UTC) - timedelta(days=2)
BEFORE = datetime.now(UTC) + timedelta(minutes=5)
STALE = timedelta(minutes=30)
LONG_AGO = datetime.now(UTC) - timedelta(hours=3)


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

    def purchase(self, name: str) -> str:
        return _sha(f"{self.slug}:{name}")


def _buyer(connection: psycopg.Connection[tuple[object, ...]], name: str) -> UUID:
    buyer = uuid4()
    connection.execute(
        "insert into identity_user(id,identity_issuer,identity_sub,email_normalized,"
        "email_verified) values(%s,%s,%s,%s,true)",
        (buyer, ISSUER, f"recon-{name}-{RUN}", f"recon.{name}.{RUN}@example.test"),
    )
    return buyer


def _event(
    connection: psycopg.Connection[tuple[object, ...]], store: Store, name: str, event_type: str
) -> UUID:
    event = uuid4()
    connection.execute(
        "insert into webhook_event(seller_organization_id,id,checkout_integration_id,"
        "webhook_endpoint_id,external_event_id_sha256,external_event_id_octets,"
        "payload_sha256,payload_octets,event_type,external_product_id_sha256,"
        "external_product_id_octets,external_purchase_id_sha256,external_purchase_id_octets,"
        "occurred_at,signed_at) values(%s,%s,%s,%s,%s,10,%s,100,%s,%s,10,%s,10,now(),now())",
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
        ),
    )
    return event


def _job(
    connection: psycopg.Connection[tuple[object, ...]],
    store: Store,
    name: str,
    status: str,
    created_at: datetime,
    error_code: str | None = None,
) -> UUID:
    event = _event(connection, store, name, "purchase.approved")
    job = connection.execute(
        "insert into app_private.fulfillment_job(seller_organization_id,webhook_event_id,"
        "event_type,external_product_id_sha256,external_purchase_id_sha256,"
        "buyer_email_normalized,status,error_code,created_at) "
        "values(%s,%s,'purchase.approved',%s,%s,%s,%s,%s,%s) returning id",
        (
            store.seller,
            event,
            _sha(f"{store.slug}:product"),
            store.purchase(name),
            f"recon.{name}@example.test",
            status,
            error_code,
            created_at,
        ),
    ).fetchone()
    assert job is not None
    return cast(UUID, job[0])


def _grant(
    connection: psycopg.Connection[tuple[object, ...]],
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
    connection: psycopg.Connection[tuple[object, ...]], store: Store, buyer: UUID, status: str
) -> UUID:
    access = uuid4()
    connection.execute(
        "insert into buyer_entitlement(seller_organization_id,id,buyer_identity_user_id,"
        "catalog_product_id,publication_id,reference_key,source,status)"
        " values(%s,%s,%s,%s,%s,%s,'billing',%s)",
        (
            store.seller,
            access,
            buyer,
            store.product,
            store.publication,
            f"recon:{access.hex}",
            status,
        ),
    )
    return access


def _seed(connection: psycopg.Connection[tuple[object, ...]], store: Store) -> None:
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

    # Divergências: uma de cada categoria.
    store.expected["fulfillment_dead_letter"] = _job(
        connection, store, "dead", "dead_letter", datetime.now(UTC), "product_mapping_missing"
    )
    store.expected["fulfillment_stalled"] = _job(connection, store, "stalled", "retry", LONG_AGO)
    store.expected["approved_without_fulfillment"] = _event(
        connection, store, "orphan", "purchase.approved"
    )
    lost = _buyer(connection, f"{store.label}-lost")
    _grant(connection, store, "lost", lost, "active")
    store.expected["grant_without_access"] = _access(connection, store, lost, "revoked")
    kept = _buyer(connection, f"{store.label}-kept")
    _grant(connection, store, "kept", kept, "refunded")
    store.expected["refunded_with_access"] = _access(connection, store, kept, "active")

    # Casos limpos, que não podem aparecer.
    _job(connection, store, "done", "completed", datetime.now(UTC))
    _job(connection, store, "fresh", "pending", datetime.now(UTC))
    _event(connection, store, "legacy-refund", "purchase.refunded")
    happy = _buyer(connection, f"{store.label}-happy")
    _grant(connection, store, "happy", happy, "active")
    _access(connection, store, happy, "active")
    refunded = _buyer(connection, f"{store.label}-refunded")
    _grant(connection, store, "refunded", refunded, "refunded")
    _access(connection, store, refunded, "revoked")
    # Estorno de uma compra, outra compra ativa do mesmo produto: o acesso fica.
    twice = _buyer(connection, f"{store.label}-twice")
    _grant(connection, store, "twice-old", twice, "refunded")
    _grant(connection, store, "twice-new", twice, "active")
    _access(connection, store, twice, "active")


@pytest.fixture(scope="module")
def stores() -> list[Store]:
    assert ADMIN_URL
    created = [Store("a"), Store("b")]
    with psycopg.connect(ADMIN_URL) as connection:
        for store in created:
            _seed(connection, store)
        connection.commit()
    return created


def _report(stores: list[Store]) -> list[dict[str, object]]:
    assert ADMIN_URL
    slugs = {store.slug for store in stores}
    rows = read_divergences(ADMIN_URL, since=SINCE, before=BEFORE, stale_after=STALE)
    return [row for row in rows if row["seller_slug"] in slugs]


def test_reports_each_divergence_once_under_its_own_store(stores: list[Store]) -> None:
    rows = _report(stores)

    found = sorted(
        (str(row["seller_slug"]), str(row["category"]), row["reference_id"]) for row in rows
    )
    expected = sorted(
        (store.slug, category, reference)
        for store in stores
        for category, reference in store.expected.items()
    )
    assert found == expected


def test_rows_carry_no_buyer_data(stores: list[Store]) -> None:
    for row in _report(stores):
        assert "email" not in " ".join(str(value) for value in row.values())
        for store in stores:
            if row["seller_slug"] == store.slug:
                assert row["seller_organization_id"] == store.seller


def test_dead_letter_keeps_its_error_code(stores: list[Store]) -> None:
    dead = [row for row in _report(stores) if row["category"] == "fulfillment_dead_letter"]
    assert {row["error_code"] for row in dead} == {"product_mapping_missing"}


def test_the_window_bounds_the_report(stores: list[Store]) -> None:
    assert ADMIN_URL
    rows = read_divergences(
        ADMIN_URL,
        since=BEFORE + timedelta(minutes=1),
        before=BEFORE + timedelta(days=1),
        stale_after=STALE,
    )
    assert [row for row in rows if row["seller_slug"] in {s.slug for s in stores}] == []


def test_running_twice_gives_the_same_report_and_writes_nothing(stores: list[Store]) -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        before = connection.execute(
            "select (select count(*) from app_private.fulfillment_job),"
            "(select count(*) from app_private.entitlement_grant),"
            "(select count(*) from buyer_entitlement),"
            "(select max(updated_at) from buyer_entitlement)"
        ).fetchone()
    first = _report(stores)
    second = _report(stores)
    with psycopg.connect(ADMIN_URL) as connection:
        after = connection.execute(
            "select (select count(*) from app_private.fulfillment_job),"
            "(select count(*) from app_private.entitlement_grant),"
            "(select count(*) from buyer_entitlement),"
            "(select max(updated_at) from buyer_entitlement)"
        ).fetchone()
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
    assert ADMIN_URL
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        read_divergences(ADMIN_URL, since=since, before=before, stale_after=stale)


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


def test_schema_ready_knows_the_head() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        ready = connection.execute("select app_private.schema_ready()").fetchone()
    assert ready == (True,)
