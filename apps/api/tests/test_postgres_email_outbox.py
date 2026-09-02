from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
import pytest

from appsell_api.email_worker import CLAIM_LIMIT, LEASE_SECONDS, WORKER_CAPABILITY, WORKER_SUBJECT

# A caixa de saída de e-mail (migration 0049) nasce dentro de
# apply_checkout_fulfillment: o grant e a intenção de enviar são a mesma
# transação. Estes testes provam o que a #356 exige — um e-mail por compra
# mesmo com replay, nenhum e-mail em reembolso, destinatário igual ao comprador
# daquele grant, lease/fence, e a faixa de lease que o worker usa dentro do que
# a função aceita (a rede que faltou na #284).

ADMIN_URL = os.getenv("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.getenv("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY = bytes.fromhex(os.getenv("APPSELL_TEST_CONTEXT_KEY_HEX", ""))

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY)),
    reason="isolated PostgreSQL environment is required",
)

SELLER = UUID("ee000000-0000-4000-8000-000000000210")
OWNER = UUID("ee000000-0000-4000-8000-000000000211")
PRODUCT = UUID("ee000000-0000-4000-8000-000000000212")
COURSE = UUID("ee000000-0000-4000-8000-000000000213")
OFFER = UUID("ee000000-0000-4000-8000-000000000214")
PUBLICATION = UUID("ee000000-0000-4000-8000-000000000215")
INTEGRATION = UUID("ee000000-0000-4000-8000-000000000216")
ENDPOINT = UUID("ee000000-0000-4000-8000-000000000217")
OUTRO_SELLER = UUID("ee000000-0000-4000-8000-000000000218")
ENDPOINT_KEY = "emailbox_0123456789abcdefghijklmnopqrst"
ENDPOINT_HASH = hashlib.sha256(ENDPOINT_KEY.encode()).hexdigest()
KEY_REFERENCE = "checkout-email-outbox-v1"
PRODUCT_REFERENCE = b"email-outbox-product-001"
PRODUCT_HASH = hashlib.sha256(PRODUCT_REFERENCE).hexdigest()
# A outra loja é completa — produto, integração, endpoint, mapeamento — para
# o teste cruzado provar que cada fila carrega o conteúdo da sua loja.
OUTRO_PRODUCT = UUID("ee000000-0000-4000-8000-000000000220")
OUTRO_COURSE = UUID("ee000000-0000-4000-8000-000000000221")
OUTRO_PUBLICATION = UUID("ee000000-0000-4000-8000-000000000222")
OUTRO_INTEGRATION = UUID("ee000000-0000-4000-8000-000000000223")
OUTRO_ENDPOINT = UUID("ee000000-0000-4000-8000-000000000224")
OUTRO_ENDPOINT_KEY = "emailbox_outra_0123456789abcdefghijklmnop"
# secret_reference é único por endpoint no banco; a outra loja tem o seu.
OUTRO_KEY_REFERENCE = "checkout-email-outbox-outra-v1"
OUTRO_ENDPOINT_HASH = hashlib.sha256(OUTRO_ENDPOINT_KEY.encode()).hexdigest()
OUTRO_PRODUCT_REFERENCE = b"email-outbox-product-outra-001"
OUTRO_PRODUCT_HASH = hashlib.sha256(OUTRO_PRODUCT_REFERENCE).hexdigest()
FULFILLMENT_SUBJECT = "fulfillment-worker-v1"
FULFILLMENT_CAPABILITY = "commerce.fulfillment.worker"
FULFILLMENT_LABEL = "pytest-email-fulfillment"
EMAIL_LABEL = "pytest-email-worker"
INGEST_CAPABILITY = "commerce.webhook.ingest"
INGEST_IDENTITY = UUID("ee000000-0000-4000-8000-000000000219")
ISSUER = "https://identity.test.autonomia.site"
RUN = uuid4().hex[:8]


@pytest.fixture(scope="module")
def fixture() -> dict[str, UUID | str]:
    assert ADMIN_URL
    fulfillment = uuid5(NAMESPACE_URL, f"{ISSUER}\0{FULFILLMENT_SUBJECT}")
    email_worker = uuid5(NAMESPACE_URL, f"{ISSUER}\0{WORKER_SUBJECT}")
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "delete from app_private.email_outbox where seller_organization_id in (%s,%s)",
            (SELLER, OUTRO_SELLER),
        )
        connection.execute(
            "delete from app_private.fulfillment_job where seller_organization_id=%s", (SELLER,)
        )
        connection.execute(
            "insert into identity_user(id,identity_issuer,identity_sub,email_normalized,"
            "email_verified) values(%s,%s,'email-outbox-owner','email.owner@example.test',true) "
            "on conflict do nothing",
            (OWNER, ISSUER),
        )
        for seller, slug, nome in (
            (SELLER, "loja-do-email", "Loja do E-mail"),
            (OUTRO_SELLER, "outra-loja-do-email", "Outra Loja"),
        ):
            connection.execute(
                "insert into seller_organization(id,slug,display_name,status) "
                "values(%s,%s,%s,'active') on conflict do nothing",
                (seller, slug, nome),
            )
        connection.execute(
            "insert into seller_membership(seller_organization_id,identity_user_id,role,status) "
            "values(%s,%s,'seller_owner','active') on conflict do nothing",
            (SELLER, OWNER),
        )
        connection.execute(
            "insert into catalog_product(seller_organization_id,id,slug,title,description,status) "
            "values(%s,%s,'curso-do-email','Curso do E-mail','Produto de teste','draft') "
            "on conflict do nothing",
            (SELLER, PRODUCT),
        )
        connection.execute(
            "insert into course(seller_organization_id,id,title,description,status,draft_version) "
            "values(%s,%s,'Curso do E-mail','Curso','draft',1) on conflict do nothing",
            (SELLER, COURSE),
        )
        connection.execute(
            "insert into product_course(seller_organization_id,catalog_product_id,course_id,"
            "position) values(%s,%s,%s,0) on conflict do nothing",
            (SELLER, PRODUCT, COURSE),
        )
        connection.execute(
            "insert into offer(seller_organization_id,id,catalog_product_id,title,summary,status,"
            "version) values(%s,%s,%s,'Oferta','Oferta','draft',1) on conflict do nothing",
            (SELLER, OFFER, PRODUCT),
        )
        connection.execute(
            "with payload as (select %s::jsonb snapshot) "
            "insert into publication(seller_organization_id,id,catalog_product_id,version,"
            "schema_version,source_sha256,snapshot,actor_identity_user_id) "
            "select %s,%s,%s,1,1,encode(digest(convert_to(snapshot::text,'UTF8'),'sha256'),'hex'),"
            "snapshot,%s from payload on conflict do nothing",
            (
                '{"schema_version":1,"product":{"title":"Curso do E-mail"}}',
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
            "insert into checkout_integration(seller_organization_id,id,adapter,display_name,"
            "status) "
            "values(%s,%s,'generic_hmac_v1','Checkout do e-mail','active') on conflict do nothing",
            (SELLER, INTEGRATION),
        )
        connection.execute(
            "insert into webhook_endpoint(seller_organization_id,id,checkout_integration_id,"
            "endpoint_key_sha256,secret_reference,status) values(%s,%s,%s,%s,%s,'active') "
            "on conflict do nothing",
            (SELLER, ENDPOINT, INTEGRATION, ENDPOINT_HASH, KEY_REFERENCE),
        )
        connection.execute(
            "insert into external_product_mapping(seller_organization_id,checkout_integration_id,"
            "external_product_id_sha256,external_product_id_octets,catalog_product_id) "
            "values(%s,%s,%s,%s,%s) on conflict do nothing",
            (SELLER, INTEGRATION, PRODUCT_HASH, len(PRODUCT_REFERENCE), PRODUCT),
        )
        connection.execute(
            "insert into seller_membership(seller_organization_id,identity_user_id,role,status) "
            "values(%s,%s,'seller_owner','active') on conflict do nothing",
            (OUTRO_SELLER, OWNER),
        )
        connection.execute(
            "insert into catalog_product(seller_organization_id,id,slug,title,description,status) "
            "values(%s,%s,'curso-da-outra','Curso da Outra','Produto de teste','draft') "
            "on conflict do nothing",
            (OUTRO_SELLER, OUTRO_PRODUCT),
        )
        connection.execute(
            "insert into course(seller_organization_id,id,title,description,status,draft_version) "
            "values(%s,%s,'Curso da Outra','Curso','draft',1) on conflict do nothing",
            (OUTRO_SELLER, OUTRO_COURSE),
        )
        connection.execute(
            "insert into product_course(seller_organization_id,catalog_product_id,course_id,"
            "position) values(%s,%s,%s,0) on conflict do nothing",
            (OUTRO_SELLER, OUTRO_PRODUCT, OUTRO_COURSE),
        )
        connection.execute(
            "with payload as (select %s::jsonb snapshot) "
            "insert into publication(seller_organization_id,id,catalog_product_id,version,"
            "schema_version,source_sha256,snapshot,actor_identity_user_id) "
            "select %s,%s,%s,1,1,encode(digest(convert_to(snapshot::text,'UTF8'),'sha256'),'hex'),"
            "snapshot,%s from payload on conflict do nothing",
            (
                '{"schema_version":1,"product":{"title":"Curso da Outra"}}',
                OUTRO_SELLER,
                OUTRO_PUBLICATION,
                OUTRO_PRODUCT,
                OWNER,
            ),
        )
        connection.execute(
            "update catalog_product set status='published',published_snapshot_id=%s "
            "where seller_organization_id=%s and id=%s",
            (OUTRO_PUBLICATION, OUTRO_SELLER, OUTRO_PRODUCT),
        )
        connection.execute(
            "insert into checkout_integration(seller_organization_id,id,adapter,display_name,"
            "status) values(%s,%s,'generic_hmac_v1','Checkout da outra','active') "
            "on conflict do nothing",
            (OUTRO_SELLER, OUTRO_INTEGRATION),
        )
        connection.execute(
            "insert into webhook_endpoint(seller_organization_id,id,checkout_integration_id,"
            "endpoint_key_sha256,secret_reference,status) values(%s,%s,%s,%s,%s,'active') "
            "on conflict do nothing",
            (
                OUTRO_SELLER,
                OUTRO_ENDPOINT,
                OUTRO_INTEGRATION,
                OUTRO_ENDPOINT_HASH,
                OUTRO_KEY_REFERENCE,
            ),
        )
        connection.execute(
            "insert into external_product_mapping(seller_organization_id,checkout_integration_id,"
            "external_product_id_sha256,external_product_id_octets,catalog_product_id) "
            "values(%s,%s,%s,%s,%s) on conflict do nothing",
            (
                OUTRO_SELLER,
                OUTRO_INTEGRATION,
                OUTRO_PRODUCT_HASH,
                len(OUTRO_PRODUCT_REFERENCE),
                OUTRO_PRODUCT,
            ),
        )
        connection.commit()
    return {"fulfillment": fulfillment, "email_worker": email_worker}


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
    *,
    event: str,
    purchase: str,
    buyer_email: str,
    event_type: str = "purchase.approved",
    seller: UUID = SELLER,
    buyer_identity_sub: str | None = None,
) -> None:
    assert RUNTIME_URL
    outra = seller == OUTRO_SELLER
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, INGEST_IDENTITY, INGEST_CAPABILITY)
        connection.execute(
            "select webhook_event_id,created from app_private.accept_checkout_webhook("
            "%s::uuid,%s::uuid,%s::text,%s::text,1::bigint,%s::text,7::smallint,"
            "%s::text,20::integer,%s::text,%s::text,11::smallint,%s::text,12::smallint,"
            "%s::timestamptz,%s::bigint,%s::uuid,%s::text,%s::text)",
            (
                seller,
                OUTRO_ENDPOINT if outra else ENDPOINT,
                OUTRO_ENDPOINT_HASH if outra else ENDPOINT_HASH,
                OUTRO_KEY_REFERENCE if outra else KEY_REFERENCE,
                hashlib.sha256(event.encode()).hexdigest(),
                hashlib.sha256(f"payload:{event}".encode()).hexdigest(),
                event_type,
                OUTRO_PRODUCT_HASH if outra else PRODUCT_HASH,
                hashlib.sha256(purchase.encode()).hexdigest(),
                "2026-09-02T10:00:00Z",
                int(time.time()),
                uuid4(),
                buyer_email,
                buyer_identity_sub,
            ),
        )
        connection.commit()


def _drain_fulfillment(fixture: dict[str, UUID | str]) -> None:
    assert RUNTIME_URL
    worker = cast(UUID, fixture["fulfillment"])
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, FULFILLMENT_CAPABILITY)
        connection.execute(
            "select app_private.ensure_fulfillment_worker(%s,%s,%s,"
            "'fulfillment-worker@system.appsell.internal',%s)",
            (worker, ISSUER, FULFILLMENT_SUBJECT, FULFILLMENT_LABEL),
        )
        connection.commit()
    for _ in range(16):
        with psycopg.connect(RUNTIME_URL) as connection:
            _context(connection, worker, FULFILLMENT_CAPABILITY)
            row = connection.execute(
                "select app_private.claim_fulfillment_jobs(%s,%s,8,30)", (worker, FULFILLMENT_LABEL)
            ).fetchone()
            connection.commit()
        jobs = cast(list[dict[str, object]], row[0] if row else [])
        if not jobs:
            return
        for job in jobs:
            with psycopg.connect(RUNTIME_URL) as connection:
                seller = UUID(str(job["seller_organization_id"]))
                _context(connection, worker, FULFILLMENT_CAPABILITY, seller)
                connection.execute(
                    "select app_private.apply_checkout_fulfillment(%s,%s,%s,%s,%s,%s,%s)",
                    (
                        worker,
                        FULFILLMENT_LABEL,
                        seller,
                        UUID(str(job["id"])),
                        int(cast(int, job["lease_fence"])),
                        uuid4(),
                        ISSUER,
                    ),
                )
                connection.commit()
    raise AssertionError("fulfillment queue did not drain")


def _outbox(
    purchase: str, seller: UUID = SELLER
) -> list[tuple[str, str, str, dict[str, object], UUID]]:
    assert ADMIN_URL
    integration = OUTRO_INTEGRATION if seller == OUTRO_SELLER else INTEGRATION
    chave = f"purchase:{integration}:{hashlib.sha256(purchase.encode()).hexdigest()}"
    with psycopg.connect(ADMIN_URL) as connection:
        rows = connection.execute(
            "select kind,recipient_email,status,payload,recipient_identity_user_id "
            "from app_private.email_outbox where seller_organization_id=%s and dedupe_key=%s",
            (seller, chave),
        ).fetchall()
    return cast(list[tuple[str, str, str, dict[str, object], UUID]], rows)


def _register_email_worker(fixture: dict[str, UUID | str]) -> UUID:
    assert RUNTIME_URL
    worker = cast(UUID, fixture["email_worker"])
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, WORKER_CAPABILITY)
        connection.execute(
            "select app_private.ensure_email_worker(%s,%s,%s,"
            "'email-worker@system.appsell.internal',%s)",
            (worker, ISSUER, WORKER_SUBJECT, EMAIL_LABEL),
        )
        connection.commit()
    return worker


def _claim(
    worker: UUID, lease: int = LEASE_SECONDS, limit: int = CLAIM_LIMIT
) -> list[dict[str, object]]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, WORKER_CAPABILITY)
        row = connection.execute(
            "select app_private.claim_email_outbox(%s,%s,%s,%s)",
            (worker, EMAIL_LABEL, limit, lease),
        ).fetchone()
        connection.commit()
    return cast(list[dict[str, object]], row[0] if row else [])


def _claim_tudo(worker: UUID) -> list[dict[str, object]]:
    """Drena a fila inteira: outras suítes deixam linhas pendentes de outras
    lojas, e um claim só devolveria as mais antigas antes das desta suíte."""
    tudo: list[dict[str, object]] = []
    for _ in range(64):
        lote = _claim(worker, limit=8)
        if not lote:
            return tudo
        tudo.extend(lote)
    raise AssertionError("email outbox did not drain")


def test_grant_enqueues_exactly_one_email_even_with_replay(fixture: dict[str, UUID | str]) -> None:
    buyer = f"buyer.{RUN}@example.test"
    purchase = f"{RUN}-compra-1"
    _accept(event=f"{RUN}-evt-1", purchase=purchase, buyer_email=buyer)
    _drain_fulfillment(fixture)
    rows = _outbox(purchase)
    assert len(rows) == 1
    kind, recipient, status, payload, recipient_id = rows[0]
    assert (kind, recipient, status) == ("purchase_confirmed", buyer, "pending")
    assert payload["schema_version"] == 1
    assert payload["product_title"] == "Curso do E-mail"
    assert payload["seller_display_name"] == "Loja do E-mail"
    assert payload["seller_slug"] == "loja-do-email"
    assert UUID(str(payload["catalog_product_id"])) == PRODUCT

    # O destinatário é o comprador daquele grant — não outro comprador, não outra loja.
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        entitlement = connection.execute(
            "select buyer_identity_user_id from buyer_entitlement where seller_organization_id=%s "
            "and id=%s",
            (SELLER, UUID(str(payload["entitlement_id"]))),
        ).fetchone()
        email = connection.execute(
            "select email_normalized from identity_user where id=%s", (recipient_id,)
        ).fetchone()
    assert entitlement == (recipient_id,)
    assert email == (buyer,)

    # Replay do mesmo evento e novo evento da mesma compra: continua um só.
    _accept(event=f"{RUN}-evt-1", purchase=purchase, buyer_email=buyer)
    _accept(event=f"{RUN}-evt-1-replay", purchase=purchase, buyer_email=buyer)
    _drain_fulfillment(fixture)
    assert len(_outbox(purchase)) == 1


def test_each_store_gets_its_own_email_and_cannot_touch_the_other(
    fixture: dict[str, UUID | str],
) -> None:
    assert RUNTIME_URL
    worker = _register_email_worker(fixture)
    buyer_a = f"loja-a.{RUN}@example.test"
    buyer_b = f"loja-b.{RUN}@example.test"
    _accept(event=f"{RUN}-evt-a", purchase=f"{RUN}-compra-a", buyer_email=buyer_a)
    _accept(
        event=f"{RUN}-evt-b", purchase=f"{RUN}-compra-b", buyer_email=buyer_b, seller=OUTRO_SELLER
    )
    _drain_fulfillment(fixture)
    (_, recipient_a, _, payload_a, _) = _outbox(f"{RUN}-compra-a")[0]
    (_, recipient_b, _, payload_b, _) = _outbox(f"{RUN}-compra-b", OUTRO_SELLER)[0]
    assert (recipient_a, payload_a["product_title"], payload_a["seller_display_name"]) == (
        buyer_a,
        "Curso do E-mail",
        "Loja do E-mail",
    )
    assert (recipient_b, payload_b["product_title"], payload_b["seller_display_name"]) == (
        buyer_b,
        "Curso da Outra",
        "Outra Loja",
    )
    # O claim devolve as duas, cada uma com a sua loja, o seu comprador e o
    # seu conteúdo — nunca cruzados.
    claimed = {
        str(item["recipient_email"]): item
        for item in _claim_tudo(worker)
        if str(item["recipient_email"]) in {buyer_a, buyer_b}
    }
    assert UUID(str(claimed[buyer_a]["seller_organization_id"])) == SELLER
    assert UUID(str(claimed[buyer_b]["seller_organization_id"])) == OUTRO_SELLER
    payload_claimed_b = cast(dict[str, object], claimed[buyer_b]["payload"])
    assert payload_claimed_b["seller_slug"] == "outra-loja-do-email"
    item_b = claimed[buyer_b]
    # Contexto da loja A não conclui nem falha o item da loja B.
    for chamada in (
        "select app_private.complete_email_outbox(%s,%s,%s,%s,%s,%s,%s)",
        "select app_private.fail_email_outbox(%s,%s,%s,%s,%s,'smtp_temporary',1,false,%s)",
    ):
        with psycopg.connect(RUNTIME_URL) as connection:
            _context(connection, worker, WORKER_CAPABILITY, SELLER)
            params: tuple[object, ...] = (
                worker,
                EMAIL_LABEL,
                OUTRO_SELLER,
                UUID(str(item_b["id"])),
                int(cast(int, item_b["lease_fence"])),
            )
            if "complete" in chamada:
                params += ("cruzado@autonomia.site", uuid4())
            else:
                params += (uuid4(),)
            with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
                connection.execute(chamada, params)
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, WORKER_CAPABILITY, OUTRO_SELLER)
        connection.execute(
            "select app_private.complete_email_outbox(%s,%s,%s,%s,%s,%s,%s)",
            (
                worker,
                EMAIL_LABEL,
                OUTRO_SELLER,
                UUID(str(item_b["id"])),
                int(cast(int, item_b["lease_fence"])),
                f"{RUN}.b@autonomia.site",
                uuid4(),
            ),
        )
        connection.commit()
    assert _outbox(f"{RUN}-compra-b", OUTRO_SELLER)[0][2] == "sent"


def test_reapproval_of_a_refunded_purchase_does_not_email(
    fixture: dict[str, UUID | str],
) -> None:
    # Reembolso chega antes de qualquer aprovação (out-of-order), outra compra
    # do mesmo comprador mantém o acesso, e a aprovação atrasada da compra
    # reembolsada passa pelo upsert da entitlement — mas não pode dizer
    # "acesso liberado" por uma compra que continua reembolsada.
    buyer = f"reembolso.{RUN}@example.test"
    reembolsada = f"{RUN}-compra-r"
    ativa = f"{RUN}-compra-s"
    _accept(
        event=f"{RUN}-evt-r-refund",
        purchase=reembolsada,
        buyer_email=buyer,
        event_type="purchase.refunded",
    )
    _drain_fulfillment(fixture)
    _accept(event=f"{RUN}-evt-s", purchase=ativa, buyer_email=buyer)
    _drain_fulfillment(fixture)
    _accept(event=f"{RUN}-evt-r-approve", purchase=reembolsada, buyer_email=buyer)
    _drain_fulfillment(fixture)
    assert len(_outbox(ativa)) == 1
    assert _outbox(reembolsada) == []


def test_recipient_is_the_identity_email_not_the_checkout_email(
    fixture: dict[str, UUID | str],
) -> None:
    # Quem tem conta recebe no e-mail da conta: é nele que o acesso aparece.
    # O e-mail digitado no checkout é o que o Stripe conhece, não onde a
    # pessoa entra.
    assert ADMIN_URL
    sub = f"cognito-{RUN}"
    conta = f"conta.{RUN}@example.test"
    checkout = f"checkout.{RUN}@example.test"
    identidade = uuid5(NAMESPACE_URL, f"{ISSUER}\0{sub}")
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into identity_user(id,identity_issuer,identity_sub,email_normalized,"
            "email_verified) values(%s,%s,%s,%s,true) on conflict do nothing",
            (identidade, ISSUER, sub, conta),
        )
        connection.commit()
    _accept(
        event=f"{RUN}-evt-conta",
        purchase=f"{RUN}-compra-conta",
        buyer_email=checkout,
        buyer_identity_sub=sub,
    )
    _drain_fulfillment(fixture)
    rows = _outbox(f"{RUN}-compra-conta")
    assert len(rows) == 1
    assert rows[0][1] == conta and rows[0][4] == identidade


def test_refund_does_not_enqueue(fixture: dict[str, UUID | str]) -> None:
    purchase = f"{RUN}-compra-reembolso"
    _accept(
        event=f"{RUN}-evt-reembolso",
        purchase=purchase,
        buyer_email=f"refund.{RUN}@example.test",
        event_type="purchase.refunded",
    )
    _drain_fulfillment(fixture)
    assert _outbox(purchase) == []


def test_lease_range_of_the_function_contains_the_worker_constants() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        definition = connection.execute(
            "select pg_get_functiondef("
            "'app_private.claim_email_outbox(uuid,text,integer,integer)'::regprocedure)"
        ).fetchone()
    assert definition is not None
    lease = re.search(r"p_lease_seconds not between (\d+) and (\d+)", str(definition[0]))
    limit = re.search(r"p_limit not between (\d+) and (\d+)", str(definition[0]))
    assert lease and limit
    assert int(lease.group(1)) <= LEASE_SECONDS <= int(lease.group(2))
    assert int(limit.group(1)) <= CLAIM_LIMIT <= int(limit.group(2))


def test_claim_complete_and_fail_respect_lease_fence_and_tenant(
    fixture: dict[str, UUID | str],
) -> None:
    assert RUNTIME_URL and ADMIN_URL
    worker = _register_email_worker(fixture)
    buyer = f"claim.{RUN}@example.test"
    purchase = f"{RUN}-compra-claim"
    _accept(event=f"{RUN}-evt-claim", purchase=purchase, buyer_email=buyer)
    _drain_fulfillment(fixture)

    claimed = [item for item in _claim_tudo(worker) if str(item["recipient_email"]) == buyer]
    assert len(claimed) == 1
    item = claimed[0]
    assert item["kind"] == "purchase_confirmed" and item["recipient_name"] is None
    assert item["attempt_count"] == 1 and int(cast(int, item["lease_fence"])) >= 1
    outbox_id = UUID(str(item["id"]))
    fence = int(cast(int, item["lease_fence"]))

    # Outro tenant no contexto: a conclusão é recusada.
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, WORKER_CAPABILITY, OUTRO_SELLER)
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.complete_email_outbox(%s,%s,%s,%s,%s,%s,%s)",
                (worker, EMAIL_LABEL, SELLER, outbox_id, fence, "id@autonomia.site", uuid4()),
            )
    # Fence errado: lease indisponível.
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, WORKER_CAPABILITY, SELLER)
        with pytest.raises(psycopg.errors.SerializationFailure):
            connection.execute(
                "select app_private.complete_email_outbox(%s,%s,%s,%s,%s,%s,%s)",
                (worker, EMAIL_LABEL, SELLER, outbox_id, fence + 1, "id@autonomia.site", uuid4()),
            )
    # Fence certo: enviado, com Message-ID e auditoria.
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, WORKER_CAPABILITY, SELLER)
        connection.execute(
            "select app_private.complete_email_outbox(%s,%s,%s,%s,%s,%s,%s)",
            (worker, EMAIL_LABEL, SELLER, outbox_id, fence, f"{RUN}.claim@autonomia.site", uuid4()),
        )
        connection.commit()
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select status,message_id,sent_at is not null,lease_owner "
            "from app_private.email_outbox "
            "where seller_organization_id=%s and id=%s",
            (SELLER, outbox_id),
        ).fetchone()
        audit = connection.execute(
            "select action,result,metadata->>'message_id' from audit_event "
            "where seller_organization_id=%s "
            "and target_type='email_outbox' and target_id=%s",
            (SELLER, outbox_id),
        ).fetchall()
    assert row == ("sent", f"{RUN}.claim@autonomia.site", True, None)
    assert audit == [("notification.email_sent", "succeeded", f"{RUN}.claim@autonomia.site")]
    # Enviado não volta a ser reivindicado.
    assert all(UUID(str(other["id"])) != outbox_id for other in _claim_tudo(worker))

    # Falha temporária volta para retry; permanente vai para dead_letter.
    purchase2 = f"{RUN}-compra-falha"
    _accept(event=f"{RUN}-evt-falha", purchase=purchase2, buyer_email=f"falha.{RUN}@example.test")
    _drain_fulfillment(fixture)
    falha = [
        i for i in _claim_tudo(worker) if str(i["recipient_email"]) == f"falha.{RUN}@example.test"
    ][0]
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, WORKER_CAPABILITY, SELLER)
        status = connection.execute(
            "select app_private.fail_email_outbox(%s,%s,%s,%s,%s,'smtp_temporary',1,false,%s)",
            (
                worker,
                EMAIL_LABEL,
                SELLER,
                UUID(str(falha["id"])),
                int(cast(int, falha["lease_fence"])),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
    assert status == ("retry",)
    time.sleep(1.1)
    de_novo = [i for i in _claim_tudo(worker) if UUID(str(i["id"])) == UUID(str(falha["id"]))][0]
    assert de_novo["attempt_count"] == 2
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, WORKER_CAPABILITY, SELLER)
        status = connection.execute(
            "select app_private.fail_email_outbox(%s,%s,%s,%s,%s,'recipient_refused',1,true,%s)",
            (
                worker,
                EMAIL_LABEL,
                SELLER,
                UUID(str(de_novo["id"])),
                int(cast(int, de_novo["lease_fence"])),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
    assert status == ("dead_letter",)
    assert all(UUID(str(i["id"])) != UUID(str(falha["id"])) for i in _claim_tudo(worker))


@pytest.mark.parametrize(
    "erro_de_canal",
    ["smtp_auth", "smtp_unreachable", "smtp_no_starttls", "smtp_no_auth", "kind_unknown"],
)
def test_channel_failures_never_dead_letter_and_give_the_attempt_back(
    fixture: dict[str, UUID | str], erro_de_canal: str
) -> None:
    # Servidor errado ou credencial recusada é falha do canal: dez quedas do
    # provedor não podem matar um e-mail legítimo.
    assert RUNTIME_URL and ADMIN_URL
    worker = _register_email_worker(fixture)
    buyer = f"canal-{erro_de_canal}.{RUN}@example.test"
    purchase = f"{RUN}-compra-{erro_de_canal}"
    _accept(event=f"{RUN}-evt-{erro_de_canal}", purchase=purchase, buyer_email=buyer)
    _drain_fulfillment(fixture)
    item = [i for i in _claim_tudo(worker) if str(i["recipient_email"]) == buyer][0]
    outbox_id = UUID(str(item["id"]))
    with psycopg.connect(ADMIN_URL) as connection:
        # Na décima tentativa: sem a isenção, isto viraria dead_letter.
        connection.execute(
            "update app_private.email_outbox set attempt_count=10 "
            "where seller_organization_id=%s and id=%s",
            (SELLER, outbox_id),
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, WORKER_CAPABILITY, SELLER)
        status = connection.execute(
            # permanent=true quando o erro é kind_unknown: é o que um worker
            # N-1 (rollback) manda para um tipo que não conhece, e o banco
            # ainda assim recusa matar a linha.
            "select app_private.fail_email_outbox(%s,%s,%s,%s,%s,%s,1,%s,%s)",
            (
                worker,
                EMAIL_LABEL,
                SELLER,
                outbox_id,
                int(cast(int, item["lease_fence"])),
                erro_de_canal,
                erro_de_canal == "kind_unknown",
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
    assert status == ("retry",)
    with psycopg.connect(ADMIN_URL) as connection:
        linha = connection.execute(
            "select status,attempt_count,error_code from app_private.email_outbox "
            "where seller_organization_id=%s and id=%s",
            (SELLER, outbox_id),
        ).fetchone()
    assert linha == ("retry", 9, erro_de_canal)
    if erro_de_canal == "kind_unknown":
        # O recuo de dez minutos é do banco, não do worker: o item não volta
        # para a fila logo em seguida, e é isso que impede o laço no rollback.
        # Uma chamada só: _claim_tudo reivindica, e chamá-lo duas vezes deixaria
        # a segunda cega para o que a primeira pegou.
        na_fila = _claim_tudo(worker)
        assert all(UUID(str(i["id"])) != outbox_id for i in na_fila)
        with psycopg.connect(ADMIN_URL) as connection:
            espera = connection.execute(
                "select available_at-clock_timestamp()>interval '9 minutes' "
                "from app_private.email_outbox where seller_organization_id=%s and id=%s",
                (SELLER, outbox_id),
            ).fetchone()
        assert espera == (True,)


def test_claim_requires_the_worker_capability_and_the_lease_range(
    fixture: dict[str, UUID | str],
) -> None:
    assert RUNTIME_URL
    worker = _register_email_worker(fixture)
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, FULFILLMENT_CAPABILITY)
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.claim_email_outbox(%s,%s,4,60)", (worker, EMAIL_LABEL)
            )
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, WORKER_CAPABILITY)
        with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
            connection.execute(
                "select app_private.claim_email_outbox(%s,%s,4,301)", (worker, EMAIL_LABEL)
            )
