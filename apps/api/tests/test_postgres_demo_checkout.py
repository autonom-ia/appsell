from __future__ import annotations

import hashlib
import hmac
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
import pytest

from appsell_api.reset_demo_tenant import load_manifest, reset_demo

ADMIN_URL = os.getenv("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.getenv("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY = bytes.fromhex(os.getenv("APPSELL_TEST_CONTEXT_KEY_HEX", ""))

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY)),
    reason="isolated PostgreSQL environment is required",
)

ENTITLEMENT = UUID("bee105e2-7809-5bf5-874a-d6332dc4a4ef")
COUPON = UUID("eb36c13c-93a1-5bcc-a50f-310572690d5f")
COUPON_HASH = hashlib.sha256(b"AUTONOMIA100").hexdigest()
WORKER_SUBJECT = "demo-checkout-worker-v1"
WORKER_LABEL = "pytest-demo-worker"


@pytest.fixture(scope="module")
def fixture() -> dict[str, UUID | str]:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("set role appsell_owner")
        row = connection.execute(
            "select registry.identity_issuer,registry.seller_organization_id,"
            "registry.buyer_identity_user_id,registry.catalog_product_id,registry.offer_id,"
            "registry.baseline_publication_id from app_private.demo_tenant_registry registry "
            "where registry.seed_key='autonomia-demo-v1'"
        ).fetchone()
        assert row is not None
        issuer, seller, buyer, product, offer, publication = row
        worker = uuid5(NAMESPACE_URL, f"{issuer}\0{WORKER_SUBJECT}")
        connection.execute("select set_config('appsell.demo_reset_key','autonomia-demo-v1',true)")
        for statement in (
            "delete from app_private.demo_checkout_request where seller_organization_id=%s",
            "delete from app_private.demo_checkout_outbox where seller_organization_id=%s",
            "delete from app_private.learning_progress_request where seller_organization_id=%s",
            "delete from lesson_progress where seller_organization_id=%s",
            "delete from course_learning_state where seller_organization_id=%s",
            "delete from learning_enrollment where seller_organization_id=%s",
            "delete from buyer_entitlement where seller_organization_id=%s",
            "delete from demo_purchase where seller_organization_id=%s",
            "delete from demo_purchase_event where seller_organization_id=%s",
            "delete from demo_order where seller_organization_id=%s",
            "delete from demo_coupon where seller_organization_id=%s",
            "delete from demo_checkout_offer where seller_organization_id=%s",
        ):
            connection.execute(statement, (seller,))
        connection.execute(
            "insert into demo_checkout_offer(seller_organization_id,offer_id,"
            "catalog_product_id,publication_id,entitlement_id,unit_amount_minor,currency,status) "
            "values(%s,%s,%s,%s,%s,9900,'BRL','active')",
            (seller, offer, product, publication, ENTITLEMENT),
        )
        connection.execute(
            "insert into demo_coupon(seller_organization_id,id,code_sha256,discount_bps,status) "
            "values(%s,%s,%s,10000,'active')",
            (seller, COUPON, COUPON_HASH),
        )
        connection.execute(
            "insert into identity_user("
            "id,identity_issuer,identity_sub,email_normalized,email_verified) "
            "values(%s,%s,%s,'checkout.worker@autonomia.site',true) "
            "on conflict(identity_issuer,identity_sub) do update set email_verified=true",
            (worker, issuer, WORKER_SUBJECT),
        )
        connection.execute(
            "insert into app_private.demo_checkout_worker_identity(identity_user_id,status) "
            "values(%s,'active') on conflict(identity_user_id) do update set status='active'",
            (worker,),
        )
        connection.commit()
    return {
        "issuer": str(issuer),
        "seller": seller,
        "buyer": buyer,
        "product": product,
        "publication": publication,
        "worker": worker,
    }


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


def _checkout(
    key: UUID, fixture: dict[str, UUID | str], *, coupon_hash: str = COUPON_HASH
) -> dict[str, object]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, fixture["buyer"], "commerce.demo.checkout.complete")  # type: ignore[arg-type]
        value = connection.execute(
            "select app_private.complete_demo_zero_checkout(%s,%s,%s,%s,%s)",
            (fixture["buyer"], coupon_hash, key, "ab" * 32, uuid4()),
        ).fetchone()
        connection.commit()
    assert value is not None and isinstance(value[0], dict)
    return value[0]


def _worker_once(fixture: dict[str, UUID | str]) -> dict[str, object]:
    assert RUNTIME_URL
    worker = fixture["worker"]
    seller = fixture["seller"]
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, "commerce.demo.checkout.worker")  # type: ignore[arg-type]
        claimed = connection.execute(
            "select app_private.claim_demo_checkout_jobs(%s,%s,1,30)",
            (worker, WORKER_LABEL),
        ).fetchone()
        connection.commit()
    assert claimed is not None and len(claimed[0]) == 1
    job = claimed[0][0]
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, "commerce.demo.checkout.worker", seller)  # type: ignore[arg-type]
        applied = connection.execute(
            "select app_private.apply_demo_checkout_entitlement(%s,%s,%s,%s,%s,%s)",
            (worker, WORKER_LABEL, seller, job["id"], job["lease_fence"], uuid4()),
        ).fetchone()
        connection.commit()
    assert applied is not None
    return cast(dict[str, object], applied[0])


def test_invalid_coupon_fails_without_order(fixture: dict[str, UUID | str]) -> None:
    assert RUNTIME_URL and ADMIN_URL
    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        _checkout(uuid4(), fixture, coupon_hash="cd" * 32)
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("set role appsell_owner")
        count = connection.execute(
            "select count(*) from demo_order where seller_organization_id=%s",
            (fixture["seller"],),
        ).fetchone()
    assert count == (0,)


def test_concurrent_checkout_creates_one_order_and_outbox(
    fixture: dict[str, UUID | str],
) -> None:
    key = uuid4()
    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(lambda _: _checkout(key, fixture), range(20)))
    assert sum(bool(result["applied"]) for result in results) == 1
    assert {result["status"] for result in results} == {"processing"}
    assert {result["provider_called"] for result in results} == {False}
    assert len({result["order_id"] for result in results}) == 1
    second_receipt = _checkout(uuid4(), fixture)
    assert second_receipt["order_id"] == results[0]["order_id"]
    assert second_receipt["applied"] is False
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("set role appsell_owner")
        counts = connection.execute(
            "select (select count(*) from demo_order where seller_organization_id=%s),"
            "(select count(*) from demo_purchase_event where seller_organization_id=%s),"
            "(select count(*) from app_private.demo_checkout_outbox "
            "where seller_organization_id=%s),"
            "(select count(*) from demo_purchase where seller_organization_id=%s),"
            "(select count(*) from buyer_entitlement where seller_organization_id=%s)",
            (fixture["seller"],) * 5,
        ).fetchone()
    assert counts == (1, 1, 1, 0, 0)


def test_worker_fence_creates_one_purchase_and_entitlement(
    fixture: dict[str, UUID | str],
) -> None:
    assert RUNTIME_URL and ADMIN_URL
    worker = fixture["worker"]
    seller = fixture["seller"]
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, "commerce.demo.checkout.worker")  # type: ignore[arg-type]
        jobs = connection.execute(
            "select app_private.claim_demo_checkout_jobs(%s,%s,4,30)",
            (worker, WORKER_LABEL),
        ).fetchone()
        connection.commit()
    assert jobs is not None and isinstance(jobs[0], list) and len(jobs[0]) == 1
    stale_job = jobs[0][0]
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("set role appsell_owner")
        connection.execute(
            "update app_private.demo_checkout_outbox set "
            "lease_expires_at=clock_timestamp()-interval '1 second' "
            "where seller_organization_id=%s and id=%s",
            (seller, stale_job["id"]),
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, "commerce.demo.checkout.worker")  # type: ignore[arg-type]
        reclaimed = connection.execute(
            "select app_private.claim_demo_checkout_jobs(%s,%s,4,30)",
            (worker, WORKER_LABEL),
        ).fetchone()
        connection.commit()
    assert reclaimed is not None and len(reclaimed[0]) == 1
    job = reclaimed[0][0]
    assert job["lease_fence"] == stale_job["lease_fence"] + 1
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, "commerce.demo.checkout.worker", seller)  # type: ignore[arg-type]
        with pytest.raises(psycopg.errors.SerializationFailure):
            connection.execute(
                "select app_private.fail_demo_checkout_job(%s,%s,%s,%s,%s,%s,1)",
                (
                    worker,
                    WORKER_LABEL,
                    seller,
                    stale_job["id"],
                    stale_job["lease_fence"],
                    "apply_retryable",
                ),
            )
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, "commerce.demo.checkout.worker", seller)  # type: ignore[arg-type]
        result = connection.execute(
            "select app_private.apply_demo_checkout_entitlement(%s,%s,%s,%s,%s,%s)",
            (worker, WORKER_LABEL, seller, job["id"], job["lease_fence"], uuid4()),
        ).fetchone()
        connection.commit()
    assert result is not None and result[0]["status"] == "approved"
    assert result[0]["provider_called"] is False
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, "commerce.demo.checkout.worker", seller)  # type: ignore[arg-type]
        with pytest.raises(psycopg.errors.SerializationFailure):
            connection.execute(
                "select app_private.apply_demo_checkout_entitlement(%s,%s,%s,%s,%s,%s)",
                (worker, WORKER_LABEL, seller, job["id"], job["lease_fence"], uuid4()),
            )
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("set role appsell_owner")
        counts = connection.execute(
            "select (select count(*) from demo_purchase where seller_organization_id=%s),"
            "(select count(*) from buyer_entitlement where source='demo_checkout' "
            "and commerce_purchase_id is not null and seller_organization_id=%s),"
            "(select count(*) from app_private.demo_checkout_outbox where status='completed' "
            "and seller_organization_id=%s)",
            (seller, seller, seller),
        ).fetchone()
    assert counts == (1, 1, 1)


def test_quote_is_buyer_scoped_and_reports_owned(fixture: dict[str, UUID | str]) -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, fixture["buyer"], "commerce.demo.checkout.read")  # type: ignore[arg-type]
        quote = connection.execute(
            "select app_private.read_demo_checkout(%s)", (fixture["buyer"],)
        ).fetchone()
    assert quote is not None and quote[0]["already_owned"] is True
    assert quote[0]["entitlement_id"] == str(ENTITLEMENT)
    with psycopg.connect(RUNTIME_URL) as connection:
        other = uuid4()
        _context(connection, other, "commerce.demo.checkout.read")
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute("select app_private.read_demo_checkout(%s)", (other,))


def test_expired_terminal_lease_is_dead_lettered_and_cannot_be_failed(
    fixture: dict[str, UUID | str],
) -> None:
    assert ADMIN_URL and RUNTIME_URL
    seller = fixture["seller"]
    worker = fixture["worker"]
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("set role appsell_owner")
        job = connection.execute(
            "update app_private.demo_checkout_outbox set status='leased',attempt_count=10,"
            "lease_owner=%s,lease_expires_at=clock_timestamp()-interval '1 second' "
            "where seller_organization_id=%s returning id,lease_fence",
            (WORKER_LABEL, seller),
        ).fetchone()
        connection.commit()
    assert job is not None
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, "commerce.demo.checkout.worker")  # type: ignore[arg-type]
        claimed = connection.execute(
            "select app_private.claim_demo_checkout_jobs(%s,%s,4,30)",
            (worker, WORKER_LABEL),
        ).fetchone()
        connection.commit()
    assert claimed == ([],)
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, "commerce.demo.checkout.worker", seller)  # type: ignore[arg-type]
        with pytest.raises(psycopg.errors.SerializationFailure):
            connection.execute(
                "select app_private.fail_demo_checkout_job(%s,%s,%s,%s,%s,%s,1)",
                (worker, WORKER_LABEL, seller, job[0], job[1], "apply_retryable"),
            )
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("set role appsell_owner")
        state = connection.execute(
            "select status,error_code,lease_owner,lease_expires_at "
            "from app_private.demo_checkout_outbox where seller_organization_id=%s and id=%s",
            (seller, job[0]),
        ).fetchone()
    assert state == ("dead_letter", "apply_unexpected", None, None)


def test_records_are_immutable_and_runtime_has_no_table_access(
    fixture: dict[str, UUID | str],
) -> None:
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("set role appsell_owner")
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute("update demo_order set currency='USD'")
    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("select count(*) from demo_order")


def test_outbox_rejects_an_event_from_another_order(
    fixture: dict[str, UUID | str],
) -> None:
    assert ADMIN_URL
    seller = fixture["seller"]
    buyers = (uuid4(), uuid4())
    orders = (uuid4(), uuid4())
    event = uuid4()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("set role appsell_owner")
        for index, buyer in enumerate(buyers):
            connection.execute(
                "insert into identity_user("
                "id,identity_issuer,identity_sub,email_normalized,email_verified) "
                "values(%s,%s,%s,%s,true)",
                (buyer, fixture["issuer"], f"mismatch-{buyer}", f"mismatch-{index}@example.test"),
            )
            connection.execute(
                "insert into demo_order(seller_organization_id,id,buyer_identity_user_id,offer_id,"
                "catalog_product_id,publication_id,coupon_id,subtotal_amount_minor,"
                "discount_amount_minor,total_amount_minor,currency,status,"
                "payment_capture_required) "
                "select %s,%s,%s,registry.offer_id,registry.catalog_product_id,"
                "registry.baseline_publication_id,%s,9900,9900,0,'BRL','approved',false "
                "from app_private.demo_tenant_registry registry where registry.seed_key=%s",
                (seller, orders[index], buyer, COUPON, "autonomia-demo-v1"),
            )
        connection.execute(
            "insert into demo_purchase_event("
            "seller_organization_id,id,order_id,event_type,event_key) "
            "values(%s,%s,%s,'purchase.approved','demo-order:'||%s::text||chr(58)||'approved')",
            (seller, event, orders[0], orders[0]),
        )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                "insert into app_private.demo_checkout_outbox(seller_organization_id,order_id,"
                "event_id,event_type,schema_version) values(%s,%s,%s,"
                "'commerce.demo_order.approved',1)",
                (seller, orders[1], event),
            )
        connection.rollback()


def test_transaction_rollback_leaves_no_second_request(
    fixture: dict[str, UUID | str],
) -> None:
    assert RUNTIME_URL and ADMIN_URL
    key = uuid4()
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, fixture["buyer"], "commerce.demo.checkout.complete")  # type: ignore[arg-type]
        connection.execute(
            "select app_private.complete_demo_zero_checkout(%s,%s,%s,%s,%s)",
            (fixture["buyer"], COUPON_HASH, key, "ef" * 32, uuid4()),
        )
        connection.rollback()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("set role appsell_owner")
        count = connection.execute(
            "select count(*) from app_private.demo_checkout_request where idempotency_key=%s",
            (key,),
        ).fetchone()
    assert count == (0,)


def test_reset_after_purchase_clears_learning_graph_before_entitlement(
    fixture: dict[str, UUID | str],
) -> None:
    assert ADMIN_URL
    manifest, manifest_sha = load_manifest()
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("set role appsell_owner")
        reset_demo(connection, manifest, manifest_sha, str(fixture["issuer"]))
        connection.commit()
        counts = connection.execute(
            "select (select count(*) from buyer_entitlement where seller_organization_id=%s),"
            "(select count(*) from learning_enrollment where seller_organization_id=%s),"
            "(select count(*) from demo_purchase where seller_organization_id=%s)",
            (fixture["seller"], fixture["seller"], fixture["seller"]),
        ).fetchone()
    assert counts == (0, 0, 0)


def test_distinct_key_race_with_worker_never_leaves_stale_processing_receipt(
    fixture: dict[str, UUID | str],
) -> None:
    first_key = uuid4()
    second_key = uuid4()
    first = _checkout(first_key, fixture)
    assert first["status"] == "processing"
    with ThreadPoolExecutor(max_workers=2) as executor:
        second_future = executor.submit(_checkout, second_key, fixture)
        worker_future = executor.submit(_worker_once, fixture)
        second = second_future.result()
        applied = worker_future.result()
    assert applied["status"] == "approved"
    replay = _checkout(second_key, fixture)
    assert replay["status"] == "approved"
    assert replay["purchase_id"] == applied["purchase_id"]
    assert replay["entitlement_id"] == applied["entitlement_id"]
    assert second["status"] in {"processing", "approved"}
