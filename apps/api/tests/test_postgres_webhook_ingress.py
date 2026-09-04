from __future__ import annotations

import hashlib
import hmac
import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import psycopg
import pytest

ADMIN_URL = os.getenv("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.getenv("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY = bytes.fromhex(os.getenv("APPSELL_TEST_CONTEXT_KEY_HEX", ""))

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY)),
    reason="isolated PostgreSQL environment is required",
)

SELLER = UUID("dd000000-0000-4000-8000-000000000001")
INTEGRATION = UUID("dd000000-0000-4000-8000-000000000002")
ENDPOINT = UUID("dd000000-0000-4000-8000-000000000003")
IDENTITY = UUID("dd000000-0000-4000-8000-000000000004")
OTHER_SELLER = UUID("dd000000-0000-4000-8000-000000000005")
OTHER_INTEGRATION = UUID("dd000000-0000-4000-8000-000000000006")
ENDPOINT_KEY = "endpoint_0123456789abcdefghijklmnopqrstuv"
ENDPOINT_HASH = hashlib.sha256(ENDPOINT_KEY.encode()).hexdigest()
KEY_REFERENCE = "checkout-demo-v1"
EVENT_HASH = hashlib.sha256(b"evt-001").hexdigest()
PAYLOAD_HASH = hashlib.sha256(b'{"event":"approved"}').hexdigest()
PRODUCT_HASH = hashlib.sha256(b"product-001").hexdigest()
PURCHASE_HASH = hashlib.sha256(b"purchase-001").hexdigest()


@pytest.fixture(scope="module", autouse=True)
def seed_webhook_endpoint() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into seller_organization(id,slug,display_name,status) "
            "values(%s,'webhook-seller','Webhook Seller','active')",
            (SELLER,),
        )
        connection.execute(
            "insert into checkout_integration("
            "seller_organization_id,id,adapter,display_name,status) "
            "values(%s,%s,'generic_hmac_v1','Generic checkout','active')",
            (SELLER, INTEGRATION),
        )
        connection.execute(
            "insert into webhook_endpoint("
            "seller_organization_id,id,checkout_integration_id,endpoint_key_sha256,"
            "secret_reference,status) values(%s,%s,%s,%s,'checkout-demo-v1','active')",
            (SELLER, ENDPOINT, INTEGRATION, ENDPOINT_HASH),
        )
        connection.execute(
            "insert into seller_organization(id,slug,display_name,status) "
            "values(%s,'webhook-other','Webhook Other','active')",
            (OTHER_SELLER,),
        )
        connection.execute(
            "insert into checkout_integration("
            "seller_organization_id,id,adapter,display_name,status) "
            "values(%s,%s,'generic_hmac_v1','Other checkout','active')",
            (OTHER_SELLER, OTHER_INTEGRATION),
        )
        connection.execute(
            "insert into webhook_endpoint("
            "seller_organization_id,id,checkout_integration_id,endpoint_key_sha256,"
            "secret_reference,status) values(%s,%s,%s,%s,'checkout-demo-v1','active')",
            (OTHER_SELLER, ENDPOINT, OTHER_INTEGRATION, ENDPOINT_HASH),
        )
        connection.commit()


def _apply_context(connection: psycopg.Connection[tuple[object, ...]]) -> None:
    issued = int(time.time())
    expires = issued + 60
    payload = f"{IDENTITY}::commerce.webhook.ingest:{issued}:{expires}"
    signature = hmac.new(CONTEXT_KEY, payload.encode(), hashlib.sha256).hexdigest()
    connection.execute(
        "select app_private.apply_request_context(%s,null,'commerce.webhook.ingest',%s,%s,%s)",
        (IDENTITY, issued, expires, signature),
    )


def _accept(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    event_hash: str = EVENT_HASH,
    payload_hash: str = PAYLOAD_HASH,
    endpoint_revision: int = 1,
    seller_id: UUID = SELLER,
    endpoint_hash: str = ENDPOINT_HASH,
    key_reference: str = KEY_REFERENCE,
) -> tuple[UUID, UUID, bool]:
    _apply_context(connection)
    row = connection.execute(
        "select * from app_private.accept_checkout_webhook("
        "%s::uuid,%s::uuid,%s::text,%s::text,%s::bigint,%s::text,7::smallint,%s::text,20::integer,"
        "'purchase.approved'::text,%s::text,11::smallint,%s::text,12::smallint,"
        "'2026-07-14T07:00:00Z'::timestamptz,%s::bigint,%s::uuid,null::text,null::text)",
        (
            seller_id,
            ENDPOINT,
            endpoint_hash,
            key_reference,
            endpoint_revision,
            event_hash,
            payload_hash,
            PRODUCT_HASH,
            PURCHASE_HASH,
            int(time.time()),
            uuid4(),
        ),
    ).fetchone()
    assert row is not None
    return row  # type: ignore[return-value]


def test_resolve_is_context_bound_and_can_select_seller_on_shared_channel() -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        assert (
            connection.execute(
                "select * from app_private.resolve_webhook_endpoint(%s)", (ENDPOINT_HASH,)
            ).fetchone()
            is None
        )
        _apply_context(connection)
        resolved = connection.execute(
            "select seller_organization_id,webhook_endpoint_id,secret_reference "
            "from app_private.resolve_webhook_endpoint(%s)",
            (ENDPOINT_HASH,),
        ).fetchone()
    assert resolved == (SELLER, ENDPOINT, "checkout-demo-v1")
    with psycopg.connect(RUNTIME_URL) as connection:
        _apply_context(connection)
        targeted = connection.execute(
            "select seller_organization_id,webhook_endpoint_id,secret_reference "
            "from app_private.resolve_webhook_endpoint(%s,%s)",
            (ENDPOINT_HASH, OTHER_SELLER),
        ).fetchone()
    assert targeted == (OTHER_SELLER, ENDPOINT, "checkout-demo-v1")


def test_concurrent_replay_creates_one_event_outbox_and_audit() -> None:
    assert RUNTIME_URL and ADMIN_URL

    def invoke(_: int) -> tuple[UUID, UUID, bool]:
        with psycopg.connect(RUNTIME_URL) as connection:
            result = _accept(connection)
            connection.commit()
            return result

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(invoke, range(12)))
    assert sum(result[2] for result in results) == 1
    assert len({result[0] for result in results}) == 1
    assert len({result[1] for result in results}) == 1
    with psycopg.connect(ADMIN_URL) as connection:
        counts = connection.execute(
            "select (select count(*) from webhook_event where seller_organization_id=%s),"
            "(select count(*) from app_private.outbox_event where seller_organization_id=%s),"
            "(select count(*) from audit_event where seller_organization_id=%s "
            "and action='commerce.webhook_received'),"
            "(select bool_and(status='blocked_retention') from app_private.outbox_event "
            "where seller_organization_id=%s)",
            (SELLER, SELLER, SELLER, SELLER),
        ).fetchone()
    assert counts == (1, 1, 1, True)


def test_divergent_replay_revision_and_rollback_fail_without_partial_state() -> None:
    assert RUNTIME_URL and ADMIN_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.errors.UniqueViolation):
            _accept(connection, payload_hash=hashlib.sha256(b"different").hexdigest())
    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            _accept(
                connection,
                event_hash=hashlib.sha256(b"evt-revision").hexdigest(),
                endpoint_revision=2,
            )
    rollback_hash = hashlib.sha256(b"evt-rollback").hexdigest()
    with psycopg.connect(ADMIN_URL) as connection:
        before = connection.execute(
            "select (select count(*) from webhook_event),"
            "(select count(*) from app_private.outbox_event),"
            "(select count(*) from audit_event where action='commerce.webhook_received')"
        ).fetchone()
    with psycopg.connect(RUNTIME_URL) as connection:
        _accept(connection, event_hash=rollback_hash)
        connection.rollback()
    with psycopg.connect(ADMIN_URL) as connection:
        after = connection.execute(
            "select (select count(*) from webhook_event),"
            "(select count(*) from app_private.outbox_event),"
            "(select count(*) from audit_event where action='commerce.webhook_received')"
        ).fetchone()
    assert after == before


def test_accept_revalidates_exact_seller_endpoint_and_hash() -> None:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            _accept(
                connection,
                event_hash=hashlib.sha256(b"evt-cross-tenant").hexdigest(),
                seller_id=uuid4(),
                endpoint_hash=ENDPOINT_HASH,
            )
    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            _accept(
                connection,
                event_hash=hashlib.sha256(b"evt-rotated-secret").hexdigest(),
                key_reference="checkout-other-v1",
            )


def test_event_is_immutable_and_runtime_has_function_only_access() -> None:
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(ADMIN_URL) as connection:
        event_id = connection.execute(
            "select id from webhook_event where seller_organization_id=%s", (SELLER,)
        ).fetchone()
        assert event_id is not None
        event_id = event_id[0]
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "update webhook_event set event_type='purchase.refunded' "
                "where seller_organization_id=%s and id=%s",
                (SELLER, event_id),
            )
    with psycopg.connect(ADMIN_URL) as connection:
        privileges = connection.execute(
            "select has_table_privilege('appsell_runtime','webhook_event','SELECT'),"
            "has_table_privilege('appsell_runtime','webhook_event','INSERT'),"
            "has_table_privilege('appsell_runtime','app_private.outbox_event','SELECT'),"
            "has_function_privilege('appsell_runtime',"
            "'app_private.resolve_webhook_endpoint(text)','EXECUTE'),"
            "has_function_privilege('appsell_runtime',"
            "'app_private.resolve_webhook_endpoint(text,uuid)','EXECUTE')",
        ).fetchone()
    assert privileges == (False, False, False, True, True)
    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("select count(*) from webhook_event")


@pytest.mark.parametrize(
    "table",
    ["checkout_integration", "webhook_endpoint", "external_product_mapping", "webhook_event"],
)
def test_readiness_requires_forced_rls(table: str) -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(f"alter table {table} no force row level security")
        assert connection.execute("select app_private.schema_ready()").fetchone() == (False,)
        connection.rollback()


def test_readiness_requires_enabled_immutable_trigger() -> None:
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute("alter table webhook_event disable trigger webhook_event_immutable")
        assert connection.execute("select app_private.schema_ready()").fetchone() == (False,)
        connection.rollback()
    with psycopg.connect(RUNTIME_URL) as connection:
        assert connection.execute("select app_private.schema_ready()").fetchone() == (True,)
