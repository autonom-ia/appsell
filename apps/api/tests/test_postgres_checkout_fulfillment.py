from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
import pytest

ADMIN_URL = os.getenv("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.getenv("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY = bytes.fromhex(os.getenv("APPSELL_TEST_CONTEXT_KEY_HEX", ""))

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY)),
    reason="isolated PostgreSQL environment is required",
)

INTEGRATION = UUID("ee000000-0000-4000-8000-000000000001")
ENDPOINT = UUID("ee000000-0000-4000-8000-000000000002")
ENDPOINT_KEY = "fulfill_0123456789abcdefghijklmnopqrstuv"
ENDPOINT_HASH = hashlib.sha256(ENDPOINT_KEY.encode()).hexdigest()
KEY_REFERENCE = "checkout-demo-v1"
PRODUCT_REFERENCE = b"fulfillment-product-001"
PRODUCT_HASH = hashlib.sha256(PRODUCT_REFERENCE).hexdigest()
UNMAPPED_PRODUCT_HASH = hashlib.sha256(b"fulfillment-product-unmapped").hexdigest()
WORKER_SUBJECT = "fulfillment-worker-v1"
WORKER_LABEL = "pytest-fulfillment-worker"
WORKER_CAPABILITY = "commerce.fulfillment.worker"
INGEST_CAPABILITY = "commerce.webhook.ingest"
INGEST_IDENTITY = UUID("ee000000-0000-4000-8000-000000000003")


@pytest.fixture(scope="module")
def fixture() -> dict[str, UUID | str]:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select registry.identity_issuer,registry.seller_organization_id,"
            "registry.catalog_product_id from app_private.demo_tenant_registry registry "
            "where registry.seed_key='autonomia-demo-v1'"
        ).fetchone()
        assert row is not None
        issuer, seller, product = row
        worker = uuid5(NAMESPACE_URL, f"{issuer}\0{WORKER_SUBJECT}")
        connection.execute(
            "delete from app_private.fulfillment_job where seller_organization_id=%s",
            (seller,),
        )
        connection.execute(
            "insert into checkout_integration("
            "seller_organization_id,id,adapter,display_name,status) "
            "values(%s,%s,'generic_hmac_v1','Fulfillment checkout','active') "
            "on conflict do nothing",
            (seller, INTEGRATION),
        )
        connection.execute(
            "insert into webhook_endpoint("
            "seller_organization_id,id,checkout_integration_id,endpoint_key_sha256,"
            "secret_reference,status) values(%s,%s,%s,%s,%s,'active') "
            "on conflict do nothing",
            (seller, ENDPOINT, INTEGRATION, ENDPOINT_HASH, KEY_REFERENCE),
        )
        connection.execute(
            "insert into external_product_mapping("
            "seller_organization_id,checkout_integration_id,external_product_id_sha256,"
            "external_product_id_octets,catalog_product_id) values(%s,%s,%s,%s,%s) "
            "on conflict do nothing",
            (seller, INTEGRATION, PRODUCT_HASH, len(PRODUCT_REFERENCE), product),
        )
        connection.commit()
    return {"issuer": issuer, "seller": seller, "product": product, "worker": worker}


def _context(
    connection: psycopg.Connection[tuple[object, ...]],
    actor: UUID,
    capability: str,
    seller: UUID | None = None,
) -> None:
    issued = int(time.time())
    expires = issued + 60
    payload = f"{actor}:{seller or ''}:{capability}:{issued}:{expires}"
    signature = hmac.new(CONTEXT_KEY, payload.encode(), hashlib.sha256).hexdigest()
    connection.execute(
        "select app_private.apply_request_context(%s,%s,%s,%s,%s,%s)",
        (actor, seller, capability, issued, expires, signature),
    )


def _accept(
    fixture: dict[str, UUID | str],
    *,
    event_reference: str,
    event_type: str = "purchase.approved",
    product_hash: str = PRODUCT_HASH,
    purchase_reference: str = "purchase-001",
    buyer_email: str | None = None,
    buyer_identity_sub: str | None = None,
) -> tuple[UUID, bool]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, INGEST_IDENTITY, INGEST_CAPABILITY)
        row = connection.execute(
            "select webhook_event_id,created from app_private.accept_checkout_webhook("
            "%s::uuid,%s::uuid,%s::text,%s::text,1::bigint,%s::text,7::smallint,"
            "%s::text,20::integer,%s::text,%s::text,11::smallint,%s::text,12::smallint,"
            "'2026-07-14T07:00:00Z'::timestamptz,%s::bigint,%s::uuid,%s::text,%s::text)",
            (
                fixture["seller"],
                ENDPOINT,
                ENDPOINT_HASH,
                KEY_REFERENCE,
                hashlib.sha256(event_reference.encode()).hexdigest(),
                hashlib.sha256(f"payload:{event_reference}".encode()).hexdigest(),
                event_type,
                product_hash,
                hashlib.sha256(purchase_reference.encode()).hexdigest(),
                int(time.time()),
                uuid4(),
                buyer_email,
                buyer_identity_sub,
            ),
        ).fetchone()
        connection.commit()
    assert row is not None
    return cast(tuple[UUID, bool], row)


def _register_worker(fixture: dict[str, UUID | str]) -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, cast(UUID, fixture["worker"]), WORKER_CAPABILITY)
        connection.execute(
            "select app_private.ensure_fulfillment_worker(%s,%s,%s,"
            "'fulfillment-worker@system.appsell.internal',%s)",
            (fixture["worker"], fixture["issuer"], WORKER_SUBJECT, WORKER_LABEL),
        )
        connection.commit()


def _drain(fixture: dict[str, UUID | str]) -> list[dict[str, object]]:
    assert RUNTIME_URL
    results: list[dict[str, object]] = []
    worker = cast(UUID, fixture["worker"])
    for _ in range(16):
        with psycopg.connect(RUNTIME_URL) as connection:
            _context(connection, worker, WORKER_CAPABILITY)
            claimed = connection.execute(
                "select app_private.claim_fulfillment_jobs(%s,%s,8,30)",
                (worker, WORKER_LABEL),
            ).fetchone()
            connection.commit()
        assert claimed is not None
        jobs = cast(list[dict[str, object]], claimed[0])
        if not jobs:
            return results
        for job in jobs:
            with psycopg.connect(RUNTIME_URL) as connection:
                seller = UUID(str(job["seller_organization_id"]))
                _context(connection, worker, WORKER_CAPABILITY, seller)
                applied = connection.execute(
                    "select app_private.apply_checkout_fulfillment(%s,%s,%s,%s,%s,%s,%s)",
                    (
                        worker,
                        WORKER_LABEL,
                        seller,
                        UUID(str(job["id"])),
                        int(cast(int, job["lease_fence"])),
                        uuid4(),
                        fixture["issuer"],
                    ),
                ).fetchone()
                connection.commit()
            assert applied is not None
            results.append(cast(dict[str, object], applied[0]))
    raise AssertionError("fulfillment queue did not drain")


def _entitlement(
    fixture: dict[str, UUID | str], purchase_reference: str
) -> tuple[UUID, str, UUID] | None:
    assert ADMIN_URL
    reference = "billing:" + hashlib.sha256(purchase_reference.encode()).hexdigest()
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select id,status,buyer_identity_user_id from buyer_entitlement "
            "where seller_organization_id=%s and reference_key=%s",
            (fixture["seller"], reference),
        ).fetchone()
    return cast("tuple[UUID, str, UUID] | None", row)


def test_v1_payload_creates_no_job_and_v2_creates_one(
    fixture: dict[str, UUID | str],
) -> None:
    assert ADMIN_URL
    _accept(fixture, event_reference="evt-v1", purchase_reference="purchase-v1")
    event_id, created = _accept(
        fixture,
        event_reference="evt-v2",
        purchase_reference="purchase-v2",
        buyer_email="buyer.v2@example.test",
    )
    assert created
    replay_id, replay_created = _accept(
        fixture,
        event_reference="evt-v2",
        purchase_reference="purchase-v2",
        buyer_email="buyer.v2@example.test",
    )
    assert replay_id == event_id and not replay_created
    with psycopg.connect(ADMIN_URL) as connection:
        rows = connection.execute(
            "select webhook_event_id,buyer_email_normalized,status "
            "from app_private.fulfillment_job where seller_organization_id=%s "
            "order by created_at",
            (fixture["seller"],),
        ).fetchall()
    assert rows == [(event_id, "buyer.v2@example.test", "pending")]


def test_worker_grants_revokes_and_respects_ordering(
    fixture: dict[str, UUID | str],
) -> None:
    assert ADMIN_URL
    _register_worker(fixture)
    results = _drain(fixture)
    assert any(result.get("applied") is True for result in results)
    granted = _entitlement(fixture, "purchase-v2")
    assert granted is not None and granted[1] == "active"
    with psycopg.connect(ADMIN_URL) as connection:
        buyer = connection.execute(
            "select identity_sub,email_normalized from identity_user where id=%s",
            (granted[2],),
        ).fetchone()
    assert buyer == ("registration:buyer.v2@example.test", "buyer.v2@example.test")

    # Replayed approval for the same purchase is idempotent.
    _accept(
        fixture,
        event_reference="evt-v2-replay",
        purchase_reference="purchase-v2",
        buyer_email="buyer.v2@example.test",
    )
    for result in _drain(fixture):
        assert result.get("status") == "completed"
    assert _entitlement(fixture, "purchase-v2") == granted

    # Refund revokes by purchase reference.
    _accept(
        fixture,
        event_reference="evt-v2-refund",
        event_type="purchase.refunded",
        purchase_reference="purchase-v2",
        buyer_email="buyer.v2@example.test",
    )
    refund_results = _drain(fixture)
    assert any(result.get("revoked") is True for result in refund_results)
    revoked = _entitlement(fixture, "purchase-v2")
    assert revoked is not None and revoked[1] == "revoked"

    # A late approval for an already-refunded purchase must not regrant.
    _accept(
        fixture,
        event_reference="evt-v2-late",
        purchase_reference="purchase-v2",
        buyer_email="buyer.v2@example.test",
    )
    late_results = _drain(fixture)
    assert any(result.get("reason") == "purchase_refunded" for result in late_results)
    still_revoked = _entitlement(fixture, "purchase-v2")
    assert still_revoked is not None and still_revoked[1] == "revoked"

    # A brand-new purchase reactivates and repoints the reference key.
    _accept(
        fixture,
        event_reference="evt-v2-repurchase",
        purchase_reference="purchase-v3",
        buyer_email="buyer.v2@example.test",
    )
    repurchase_results = _drain(fixture)
    assert any(result.get("applied") is True for result in repurchase_results)
    reactivated = _entitlement(fixture, "purchase-v3")
    assert reactivated is not None
    assert reactivated[0] == granted[0]
    assert reactivated[1] == "active"


def test_explicit_identity_sub_binds_to_that_user(fixture: dict[str, UUID | str]) -> None:
    assert ADMIN_URL
    _register_worker(fixture)
    _accept(
        fixture,
        event_reference="evt-sub",
        purchase_reference="purchase-sub",
        buyer_email="buyer.sub@example.test",
        buyer_identity_sub="cognito-sub-fulfillment-001",
    )
    results = _drain(fixture)
    assert any(result.get("applied") is True for result in results)
    granted = _entitlement(fixture, "purchase-sub")
    assert granted is not None
    with psycopg.connect(ADMIN_URL) as connection:
        buyer = connection.execute(
            "select identity_sub from identity_user where id=%s", (granted[2],)
        ).fetchone()
    assert buyer == ("cognito-sub-fulfillment-001",)


def test_unmapped_product_dead_letters(fixture: dict[str, UUID | str]) -> None:
    assert ADMIN_URL
    _register_worker(fixture)
    _accept(
        fixture,
        event_reference="evt-unmapped",
        product_hash=UNMAPPED_PRODUCT_HASH,
        purchase_reference="purchase-unmapped",
        buyer_email="buyer.unmapped@example.test",
    )
    results = _drain(fixture)
    assert any(result.get("error_code") == "product_mapping_missing" for result in results)
    assert _entitlement(fixture, "purchase-unmapped") is None
    with psycopg.connect(ADMIN_URL) as connection:
        status = connection.execute(
            "select status from app_private.fulfillment_job "
            "where seller_organization_id=%s and error_code='product_mapping_missing'",
            (fixture["seller"],),
        ).fetchone()
    assert status == ("dead_letter",)


def test_worker_requires_active_allowlist_and_runtime_has_no_table_access(
    fixture: dict[str, UUID | str],
) -> None:
    assert ADMIN_URL and RUNTIME_URL
    _register_worker(fixture)
    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("select count(*) from app_private.fulfillment_job")
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, cast(UUID, fixture["worker"]), "commerce.demo.checkout.worker")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.claim_fulfillment_jobs(%s,%s,8,30)",
                (fixture["worker"], WORKER_LABEL),
            )
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update app_private.fulfillment_worker_identity set status='revoked' "
            "where identity_user_id=%s",
            (fixture["worker"],),
        )
        connection.commit()
    try:
        with psycopg.connect(RUNTIME_URL) as connection:
            _context(connection, cast(UUID, fixture["worker"]), WORKER_CAPABILITY)
            with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
                connection.execute(
                    "select app_private.claim_fulfillment_jobs(%s,%s,8,30)",
                    (fixture["worker"], WORKER_LABEL),
                )
    finally:
        with psycopg.connect(ADMIN_URL) as connection:
            connection.execute(
                "update app_private.fulfillment_worker_identity set status='active' "
                "where identity_user_id=%s",
                (fixture["worker"],),
            )
            connection.commit()
