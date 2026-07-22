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

SELLER = UUID("ee000000-0000-4000-8000-000000000010")
OWNER = UUID("ee000000-0000-4000-8000-000000000011")
PRODUCT = UUID("ee000000-0000-4000-8000-000000000012")
COURSE = UUID("ee000000-0000-4000-8000-000000000013")
OFFER = UUID("ee000000-0000-4000-8000-000000000014")
PUBLICATION = UUID("ee000000-0000-4000-8000-000000000015")
INTEGRATION = UUID("ee000000-0000-4000-8000-000000000001")
ENDPOINT = UUID("ee000000-0000-4000-8000-000000000002")
ENDPOINT_KEY = "fulfill_0123456789abcdefghijklmnopqrstuv"
ENDPOINT_HASH = hashlib.sha256(ENDPOINT_KEY.encode()).hexdigest()
KEY_REFERENCE = "checkout-fulfillment-v1"
PRODUCT_REFERENCE = b"fulfillment-product-001"
PRODUCT_HASH = hashlib.sha256(PRODUCT_REFERENCE).hexdigest()
UNMAPPED_PRODUCT_HASH = hashlib.sha256(b"fulfillment-product-unmapped").hexdigest()
WORKER_SUBJECT = "fulfillment-worker-v1"
WORKER_LABEL = "pytest-fulfillment-worker"
WORKER_CAPABILITY = "commerce.fulfillment.worker"
INGEST_CAPABILITY = "commerce.webhook.ingest"
INGEST_IDENTITY = UUID("ee000000-0000-4000-8000-000000000003")
# Referências únicas por execução: webhook_event é imutável, então reruns na
# mesma base não podem colidir com eventos de execuções anteriores.
RUN = uuid4().hex[:8]


@pytest.fixture(scope="module")
def fixture() -> dict[str, UUID | str]:
    # Self-contained tenant: nothing here touches the demo tenant, so demo
    # reset/checkout suites stay unaffected (an external_product_mapping row on
    # the demo product would block reset_demo deletes via FK).
    assert ADMIN_URL
    issuer = "https://identity.test.autonomia.site"
    worker = uuid5(NAMESPACE_URL, f"{issuer}\0{WORKER_SUBJECT}")
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "delete from app_private.fulfillment_job where seller_organization_id=%s",
            (SELLER,),
        )
        connection.execute(
            "insert into identity_user(id,identity_issuer,identity_sub,"
            "email_normalized,email_verified) "
            "values(%s,%s,'fulfillment-owner','fulfillment.owner@example.test',true) "
            "on conflict do nothing",
            (OWNER, issuer),
        )
        connection.execute(
            "insert into seller_organization(id,slug,display_name,status) "
            "values(%s,'fulfillment-seller','Fulfillment Seller','active') "
            "on conflict do nothing",
            (SELLER,),
        )
        connection.execute(
            "insert into seller_membership(seller_organization_id,identity_user_id,"
            "role,status) values(%s,%s,'seller_owner','active') on conflict do nothing",
            (SELLER, OWNER),
        )
        connection.execute(
            "insert into catalog_product(seller_organization_id,id,slug,title,"
            "description,status) values(%s,%s,'fulfillment-product',"
            "'Fulfillment product','Produto de teste','draft') on conflict do nothing",
            (SELLER, PRODUCT),
        )
        connection.execute(
            "insert into course(seller_organization_id,id,title,description,status,"
            "draft_version) values(%s,%s,'Fulfillment course','Curso','draft',1) "
            "on conflict do nothing",
            (SELLER, COURSE),
        )
        connection.execute(
            "insert into product_course(seller_organization_id,catalog_product_id,"
            "course_id,position) values(%s,%s,%s,0) on conflict do nothing",
            (SELLER, PRODUCT, COURSE),
        )
        connection.execute(
            "insert into offer(seller_organization_id,id,catalog_product_id,title,"
            "summary,status,version) values(%s,%s,%s,'Fulfillment offer','Oferta',"
            "'draft',1) on conflict do nothing",
            (SELLER, OFFER, PRODUCT),
        )
        connection.execute(
            "with payload as (select %s::jsonb snapshot) "
            "insert into publication(seller_organization_id,id,catalog_product_id,"
            "version,schema_version,source_sha256,snapshot,actor_identity_user_id) "
            "select %s,%s,%s,1,1,"
            "encode(digest(convert_to(snapshot::text,'UTF8'),'sha256'),'hex'),"
            "snapshot,%s from payload on conflict do nothing",
            (
                '{"schema_version":1,"product":{"title":"Fulfillment product"}}',
                SELLER,
                PUBLICATION,
                PRODUCT,
                OWNER,
            ),
        )
        connection.execute(
            "update catalog_product set status='published',published_snapshot_id=%s "
            "where seller_organization_id=%s and id=%s",
            (PUBLICATION, SELLER, PRODUCT),
        )
        connection.execute(
            "insert into checkout_integration("
            "seller_organization_id,id,adapter,display_name,status) "
            "values(%s,%s,'generic_hmac_v1','Fulfillment checkout','active') "
            "on conflict do nothing",
            (SELLER, INTEGRATION),
        )
        connection.execute(
            "insert into webhook_endpoint("
            "seller_organization_id,id,checkout_integration_id,endpoint_key_sha256,"
            "secret_reference,status) values(%s,%s,%s,%s,%s,'active') "
            "on conflict do nothing",
            (SELLER, ENDPOINT, INTEGRATION, ENDPOINT_HASH, KEY_REFERENCE),
        )
        connection.execute(
            "insert into external_product_mapping("
            "seller_organization_id,checkout_integration_id,external_product_id_sha256,"
            "external_product_id_octets,catalog_product_id) values(%s,%s,%s,%s,%s) "
            "on conflict do nothing",
            (SELLER, INTEGRATION, PRODUCT_HASH, len(PRODUCT_REFERENCE), PRODUCT),
        )
        connection.commit()
    return {"issuer": issuer, "seller": SELLER, "product": PRODUCT, "worker": worker}


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
    occurred_at: str = "2026-07-14T07:00:00Z",
) -> tuple[UUID, bool]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, INGEST_IDENTITY, INGEST_CAPABILITY)
        row = connection.execute(
            "select webhook_event_id,created from app_private.accept_checkout_webhook("
            "%s::uuid,%s::uuid,%s::text,%s::text,1::bigint,%s::text,7::smallint,"
            "%s::text,20::integer,%s::text,%s::text,11::smallint,%s::text,12::smallint,"
            "%s::timestamptz,%s::bigint,%s::uuid,%s::text,%s::text)",
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
                occurred_at,
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
    # A entitlement é keyed por (buyer,produto); a resolução aqui é via ledger:
    # acha o grant desta compra e retorna a entitlement do seu (buyer,produto).
    assert ADMIN_URL
    purchase_sha256 = hashlib.sha256(purchase_reference.encode()).hexdigest()
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select entitlement.id,entitlement.status,entitlement.buyer_identity_user_id "
            "from buyer_entitlement entitlement "
            "join app_private.entitlement_grant ledger "
            "on ledger.seller_organization_id=entitlement.seller_organization_id "
            "and ledger.buyer_identity_user_id=entitlement.buyer_identity_user_id "
            "and ledger.catalog_product_id=entitlement.catalog_product_id "
            "where entitlement.seller_organization_id=%s "
            "and ledger.external_purchase_id_sha256=%s",
            (fixture["seller"], purchase_sha256),
        ).fetchone()
    return cast("tuple[UUID, str, UUID] | None", row)


def test_v1_payload_creates_no_job_and_v2_creates_one(
    fixture: dict[str, UUID | str],
) -> None:
    assert ADMIN_URL
    v1_event_id, _ = _accept(
        fixture, event_reference=f"{RUN}-evt-v1", purchase_reference=f"{RUN}-purchase-v1"
    )
    event_id, created = _accept(
        fixture,
        event_reference=f"{RUN}-evt-v2",
        purchase_reference=f"{RUN}-purchase-v2",
        buyer_email=f"buyer.v2.{RUN}@example.test",
    )
    assert created
    replay_id, replay_created = _accept(
        fixture,
        event_reference=f"{RUN}-evt-v2",
        purchase_reference=f"{RUN}-purchase-v2",
        buyer_email=f"buyer.v2.{RUN}@example.test",
    )
    assert replay_id == event_id and not replay_created
    with psycopg.connect(ADMIN_URL) as connection:
        rows = connection.execute(
            "select webhook_event_id,buyer_email_normalized,status "
            "from app_private.fulfillment_job where seller_organization_id=%s "
            "and webhook_event_id in (%s,%s) order by created_at",
            (fixture["seller"], event_id, v1_event_id),
        ).fetchall()
    assert rows == [(event_id, f"buyer.v2.{RUN}@example.test", "pending")]


def test_worker_grants_revokes_and_respects_ordering(
    fixture: dict[str, UUID | str],
) -> None:
    assert ADMIN_URL
    _register_worker(fixture)
    results = _drain(fixture)
    assert any(result.get("applied") is True for result in results)
    granted = _entitlement(fixture, f"{RUN}-purchase-v2")
    assert granted is not None and granted[1] == "active"
    with psycopg.connect(ADMIN_URL) as connection:
        buyer = connection.execute(
            "select identity_sub,email_normalized from identity_user where id=%s",
            (granted[2],),
        ).fetchone()
    assert buyer == (f"registration:buyer.v2.{RUN}@example.test", f"buyer.v2.{RUN}@example.test")

    # Replayed approval for the same purchase is idempotent.
    _accept(
        fixture,
        event_reference=f"{RUN}-evt-v2-replay",
        purchase_reference=f"{RUN}-purchase-v2",
        buyer_email=f"buyer.v2.{RUN}@example.test",
    )
    for result in _drain(fixture):
        assert result.get("status") == "completed"
    assert _entitlement(fixture, f"{RUN}-purchase-v2") == granted

    # Refund revokes by purchase reference.
    _accept(
        fixture,
        event_reference=f"{RUN}-evt-v2-refund",
        event_type="purchase.refunded",
        purchase_reference=f"{RUN}-purchase-v2",
        buyer_email=f"buyer.v2.{RUN}@example.test",
    )
    refund_results = _drain(fixture)
    assert any(result.get("revoked") is True for result in refund_results)
    revoked = _entitlement(fixture, f"{RUN}-purchase-v2")
    assert revoked is not None and revoked[1] == "revoked"

    # A late approval for an already-refunded purchase must not regrant.
    _accept(
        fixture,
        event_reference=f"{RUN}-evt-v2-late",
        purchase_reference=f"{RUN}-purchase-v2",
        buyer_email=f"buyer.v2.{RUN}@example.test",
    )
    late_results = _drain(fixture)
    assert any(result.get("reason") == "purchase_refunded" for result in late_results)
    still_revoked = _entitlement(fixture, f"{RUN}-purchase-v2")
    assert still_revoked is not None and still_revoked[1] == "revoked"

    # A brand-new purchase reactivates and repoints the reference key.
    _accept(
        fixture,
        event_reference=f"{RUN}-evt-v2-repurchase",
        purchase_reference=f"{RUN}-purchase-v3",
        buyer_email=f"buyer.v2.{RUN}@example.test",
    )
    repurchase_results = _drain(fixture)
    assert any(result.get("applied") is True for result in repurchase_results)
    reactivated = _entitlement(fixture, f"{RUN}-purchase-v3")
    assert reactivated is not None
    assert reactivated[0] == granted[0]
    assert reactivated[1] == "active"


def test_granted_buyer_identity_uses_canonical_uuid(fixture: dict[str, UUID | str]) -> None:
    # A identidade criada pelo fulfillment precisa ter o MESMO id que o login
    # Cognito produz (uuid5(NAMESPACE_URL, issuer\\0sub)), senão o comprador
    # não alcança o próprio entitlement depois de logar.
    assert ADMIN_URL
    _register_worker(fixture)
    _accept(
        fixture,
        event_reference=f"{RUN}-evt-canon",
        purchase_reference=f"{RUN}-purchase-canon",
        buyer_email=f"buyer.canon.{RUN}@example.test",
    )
    for result in _drain(fixture):
        assert result.get("status") == "completed"
    granted = _entitlement(fixture, f"{RUN}-purchase-canon")
    assert granted is not None
    issuer = cast(str, fixture["issuer"])
    expected = uuid5(NAMESPACE_URL, f"{issuer}\0registration:buyer.canon.{RUN}@example.test")
    assert granted[2] == expected


def test_refund_before_approval_of_same_purchase_grants_no_access(
    fixture: dict[str, UUID | str],
) -> None:
    # Ordem inversa: o refund chega e é processado ANTES do approval da mesma
    # compra. O approval não pode conceder acesso.
    assert ADMIN_URL
    _register_worker(fixture)
    _accept(
        fixture,
        event_reference=f"{RUN}-evt-rev-refund",
        event_type="purchase.refunded",
        purchase_reference=f"{RUN}-purchase-rev",
        buyer_email=f"buyer.rev.{RUN}@example.test",
    )
    for result in _drain(fixture):
        assert result.get("status") == "completed"
    _accept(
        fixture,
        event_reference=f"{RUN}-evt-rev-approve",
        purchase_reference=f"{RUN}-purchase-rev",
        buyer_email=f"buyer.rev.{RUN}@example.test",
    )
    approve_results = _drain(fixture)
    assert any(result.get("reason") == "purchase_refunded" for result in approve_results)
    assert _entitlement(fixture, f"{RUN}-purchase-rev") is None


def test_refund_of_superseded_purchase_does_not_revoke(
    fixture: dict[str, UUID | str],
) -> None:
    # Compra A concede; compra B (re-compra) assume a mesma entitlement e a
    # reference_key. Refund de A (superada) NÃO revoga; refund de B revoga.
    assert ADMIN_URL
    _register_worker(fixture)
    _accept(
        fixture,
        event_reference=f"{RUN}-evt-sup-a",
        purchase_reference=f"{RUN}-purchase-sup-a",
        buyer_email=f"buyer.sup.{RUN}@example.test",
    )
    _drain(fixture)
    _accept(
        fixture,
        event_reference=f"{RUN}-evt-sup-b",
        purchase_reference=f"{RUN}-purchase-sup-b",
        buyer_email=f"buyer.sup.{RUN}@example.test",
    )
    _drain(fixture)
    entitlement_b = _entitlement(fixture, f"{RUN}-purchase-sup-b")
    assert entitlement_b is not None and entitlement_b[1] == "active"

    _accept(
        fixture,
        event_reference=f"{RUN}-evt-sup-a-refund",
        event_type="purchase.refunded",
        purchase_reference=f"{RUN}-purchase-sup-a",
        buyer_email=f"buyer.sup.{RUN}@example.test",
    )
    refund_a_results = _drain(fixture)
    assert any(result.get("reason") == "nothing_to_revoke" for result in refund_a_results)
    still_active = _entitlement(fixture, f"{RUN}-purchase-sup-b")
    assert still_active is not None and still_active[1] == "active"

    _accept(
        fixture,
        event_reference=f"{RUN}-evt-sup-b-refund",
        event_type="purchase.refunded",
        purchase_reference=f"{RUN}-purchase-sup-b",
        buyer_email=f"buyer.sup.{RUN}@example.test",
    )
    refund_b_results = _drain(fixture)
    assert any(result.get("revoked") is True for result in refund_b_results)
    revoked = _entitlement(fixture, f"{RUN}-purchase-sup-b")
    assert revoked is not None and revoked[1] == "revoked"


def test_late_replay_of_older_purchase_does_not_steal_ownership(
    fixture: dict[str, UUID | str],
) -> None:
    # A (mais antiga) concede; B (mais nova) assume a posse. Um replay tardio de
    # A (novo event id, occurred_at antigo) NAO pode roubar a posse de B; e o
    # refund de A depois disso nao revoga o acesso pago por B.
    assert ADMIN_URL
    _register_worker(fixture)
    _accept(
        fixture,
        event_reference=f"{RUN}-evt-old-a",
        purchase_reference=f"{RUN}-purchase-old-a",
        buyer_email=f"buyer.mono.{RUN}@example.test",
        occurred_at="2026-07-14T07:00:00Z",
    )
    _drain(fixture)
    _accept(
        fixture,
        event_reference=f"{RUN}-evt-new-b",
        purchase_reference=f"{RUN}-purchase-new-b",
        buyer_email=f"buyer.mono.{RUN}@example.test",
        occurred_at="2026-07-15T07:00:00Z",
    )
    _drain(fixture)
    # Replay semântico de A com event id novo, mas occurred_at antigo.
    _accept(
        fixture,
        event_reference=f"{RUN}-evt-old-a-replay",
        purchase_reference=f"{RUN}-purchase-old-a",
        buyer_email=f"buyer.mono.{RUN}@example.test",
        occurred_at="2026-07-14T07:00:00Z",
    )
    _drain(fixture)
    # Ownership continua com B.
    owned_by_b = _entitlement(fixture, f"{RUN}-purchase-new-b")
    assert owned_by_b is not None and owned_by_b[1] == "active"

    _accept(
        fixture,
        event_reference=f"{RUN}-evt-old-a-refund",
        event_type="purchase.refunded",
        purchase_reference=f"{RUN}-purchase-old-a",
        buyer_email=f"buyer.mono.{RUN}@example.test",
        occurred_at="2026-07-16T07:00:00Z",
    )
    refund_results = _drain(fixture)
    assert any(result.get("reason") == "nothing_to_revoke" for result in refund_results)
    still_active = _entitlement(fixture, f"{RUN}-purchase-new-b")
    assert still_active is not None and still_active[1] == "active"


def test_two_active_purchases_refund_newer_keeps_access(
    fixture: dict[str, UUID | str],
) -> None:
    # Duas compras ativas (mesmo buyer/produto): reembolsar a MAIS NOVA não
    # revoga, porque a antiga ainda justifica o acesso (só o ledger acerta isso).
    assert ADMIN_URL
    _register_worker(fixture)
    _accept(
        fixture,
        event_reference=f"{RUN}-evt-conc-a",
        purchase_reference=f"{RUN}-purchase-conc-a",
        buyer_email=f"buyer.conc.{RUN}@example.test",
        occurred_at="2026-07-14T07:00:00Z",
    )
    _drain(fixture)
    _accept(
        fixture,
        event_reference=f"{RUN}-evt-conc-b",
        purchase_reference=f"{RUN}-purchase-conc-b",
        buyer_email=f"buyer.conc.{RUN}@example.test",
        occurred_at="2026-07-15T07:00:00Z",
    )
    _drain(fixture)
    _accept(
        fixture,
        event_reference=f"{RUN}-evt-conc-b-refund",
        event_type="purchase.refunded",
        purchase_reference=f"{RUN}-purchase-conc-b",
        buyer_email=f"buyer.conc.{RUN}@example.test",
        occurred_at="2026-07-16T07:00:00Z",
    )
    refund_results = _drain(fixture)
    assert any(result.get("reason") == "nothing_to_revoke" for result in refund_results)
    still_active = _entitlement(fixture, f"{RUN}-purchase-conc-a")
    assert still_active is not None and still_active[1] == "active"
    # Reembolsar também a antiga agora revoga (nenhum grant ativo resta).
    _accept(
        fixture,
        event_reference=f"{RUN}-evt-conc-a-refund",
        event_type="purchase.refunded",
        purchase_reference=f"{RUN}-purchase-conc-a",
        buyer_email=f"buyer.conc.{RUN}@example.test",
        occurred_at="2026-07-17T07:00:00Z",
    )
    refund_a_results = _drain(fixture)
    assert any(result.get("revoked") is True for result in refund_a_results)
    revoked = _entitlement(fixture, f"{RUN}-purchase-conc-a")
    assert revoked is not None and revoked[1] == "revoked"


def test_explicit_identity_sub_binds_to_that_user(fixture: dict[str, UUID | str]) -> None:
    assert ADMIN_URL
    _register_worker(fixture)
    _accept(
        fixture,
        event_reference=f"{RUN}-evt-sub",
        purchase_reference=f"{RUN}-purchase-sub",
        buyer_email=f"buyer.sub.{RUN}@example.test",
        buyer_identity_sub=f"cognito-sub-{RUN}",
    )
    results = _drain(fixture)
    assert any(result.get("applied") is True for result in results)
    granted = _entitlement(fixture, f"{RUN}-purchase-sub")
    assert granted is not None
    with psycopg.connect(ADMIN_URL) as connection:
        buyer = connection.execute(
            "select identity_sub from identity_user where id=%s", (granted[2],)
        ).fetchone()
    assert buyer == (f"cognito-sub-{RUN}",)


def test_registration_namespace_is_reserved(fixture: dict[str, UUID | str]) -> None:
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _accept(
            fixture,
            event_reference=f"{RUN}-evt-reserved",
            purchase_reference=f"{RUN}-purchase-reserved",
            buyer_email=f"attacker.{RUN}@example.test",
            buyer_identity_sub="registration:victim@example.test",
        )


def test_unmapped_product_dead_letters(fixture: dict[str, UUID | str]) -> None:
    assert ADMIN_URL
    _register_worker(fixture)
    _accept(
        fixture,
        event_reference=f"{RUN}-evt-unmapped",
        product_hash=UNMAPPED_PRODUCT_HASH,
        purchase_reference=f"{RUN}-purchase-unmapped",
        buyer_email=f"buyer.unmapped.{RUN}@example.test",
    )
    results = _drain(fixture)
    assert any(result.get("error_code") == "product_mapping_missing" for result in results)
    assert _entitlement(fixture, f"{RUN}-purchase-unmapped") is None
    with psycopg.connect(ADMIN_URL) as connection:
        statuses = connection.execute(
            "select status from app_private.fulfillment_job "
            "where seller_organization_id=%s and error_code='product_mapping_missing'",
            (fixture["seller"],),
        ).fetchall()
    assert ("dead_letter",) in statuses


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
