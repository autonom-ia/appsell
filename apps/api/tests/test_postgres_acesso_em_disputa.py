"""Acesso em compra com disputa de pagamento, contra o Postgres de verdade (#542).

Cada teste diz o defeito que pega. Os eventos entram pelo ingresso real
(`accept_checkout_webhook`) e são aplicados pelo protocolo do worker
(`claim_fulfillment_jobs` + `apply_checkout_fulfillment`), como em produção.
"""

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
    not ADMIN_URL or not RUNTIME_URL or not CONTEXT_KEY,
    reason="isolated PostgreSQL environment is required",
)

SELLER = UUID("e5420000-0000-4000-8000-000000000010")
OFF_SELLER = UUID("e5420000-0000-4000-8000-000000000020")
OWNER = UUID("e5420000-0000-4000-8000-000000000011")
ENDPOINT_KEY = "dispute_0123456789abcdefghijklmnopqrstuv"
KEY_REFERENCE = "checkout-dispute-v1"
PRODUCT_REFERENCE = b"dispute-product-001"
PRODUCT_HASH = hashlib.sha256(PRODUCT_REFERENCE).hexdigest()
WORKER_SUBJECT = "fulfillment-worker-v1"
WORKER_LABEL = "pytest-dispute-worker"
WORKER_CAPABILITY = "commerce.fulfillment.worker"
INGEST_CAPABILITY = "commerce.webhook.ingest"
INGEST_IDENTITY = UUID("e5420000-0000-4000-8000-000000000003")
ISSUER = "https://identity.test.autonomia.site"
OPENED = "purchase.dispute_opened"
WON = "purchase.dispute_won"
LOST = "purchase.dispute_lost"
APPROVED = "purchase.approved"
REFUNDED = "purchase.refunded"
# webhook_event é imutável: referências únicas por execução.
RUN = uuid4().hex[:8]


def _ids(seller: UUID) -> dict[str, UUID]:
    base = str(seller)[:-2]
    return {
        "seller": seller,
        "product": UUID(base + "12"),
        "course": UUID(base + "13"),
        "offer": UUID(base + "14"),
        "publication": UUID(base + "15"),
        "integration": UUID(base + "01"),
        "endpoint": UUID(base + "02"),
    }


def _endpoint_hash(seller: UUID) -> str:
    return hashlib.sha256(f"{ENDPOINT_KEY}:{seller}".encode()).hexdigest()


def _seed_seller(connection: psycopg.Connection[tuple[object, ...]], seller: UUID) -> None:
    ids = _ids(seller)
    slug = f"dispute-seller-{str(seller)[-2:]}"
    connection.execute(
        "delete from app_private.fulfillment_job where seller_organization_id=%s", (seller,)
    )
    connection.execute(
        "insert into seller_organization(id,slug,display_name,status) "
        "values(%s,%s,'Dispute Seller','active') on conflict do nothing",
        (seller, slug),
    )
    connection.execute(
        "insert into seller_membership(seller_organization_id,identity_user_id,"
        "role,status) values(%s,%s,'seller_owner','active') on conflict do nothing",
        (seller, OWNER),
    )
    connection.execute(
        "insert into catalog_product(seller_organization_id,id,slug,title,"
        "description,status) values(%s,%s,'dispute-product',"
        "'Dispute product','Produto de teste','draft') on conflict do nothing",
        (seller, ids["product"]),
    )
    connection.execute(
        "insert into course(seller_organization_id,id,title,description,status,"
        "draft_version) values(%s,%s,'Dispute course','Curso','draft',1) "
        "on conflict do nothing",
        (seller, ids["course"]),
    )
    connection.execute(
        "insert into product_course(seller_organization_id,catalog_product_id,"
        "course_id,position) values(%s,%s,%s,0) on conflict do nothing",
        (seller, ids["product"], ids["course"]),
    )
    connection.execute(
        "insert into offer(seller_organization_id,id,catalog_product_id,title,"
        "summary,status,version) values(%s,%s,%s,'Dispute offer','Oferta',"
        "'draft',1) on conflict do nothing",
        (seller, ids["offer"], ids["product"]),
    )
    connection.execute(
        "with payload as (select %s::jsonb snapshot) "
        "insert into publication(seller_organization_id,id,catalog_product_id,"
        "version,schema_version,source_sha256,snapshot,actor_identity_user_id) "
        "select %s,%s,%s,1,1,"
        "encode(digest(convert_to(snapshot::text,'UTF8'),'sha256'),'hex'),"
        "snapshot,%s from payload on conflict do nothing",
        (
            '{"schema_version":1,"product":{"title":"Dispute product"}}',
            seller,
            ids["publication"],
            ids["product"],
            OWNER,
        ),
    )
    connection.execute(
        "update catalog_product set status='published',published_snapshot_id=%s "
        "where seller_organization_id=%s and id=%s",
        (ids["publication"], seller, ids["product"]),
    )
    connection.execute(
        "insert into checkout_integration("
        "seller_organization_id,id,adapter,display_name,status) "
        "values(%s,%s,'generic_hmac_v1','Dispute checkout','active') "
        "on conflict do nothing",
        (seller, ids["integration"]),
    )
    connection.execute(
        "insert into webhook_endpoint("
        "seller_organization_id,id,checkout_integration_id,endpoint_key_sha256,"
        "secret_reference,status) values(%s,%s,%s,%s,%s,'active') "
        "on conflict do nothing",
        (seller, ids["endpoint"], ids["integration"], _endpoint_hash(seller), KEY_REFERENCE),
    )
    connection.execute(
        "insert into external_product_mapping("
        "seller_organization_id,checkout_integration_id,external_product_id_sha256,"
        "external_product_id_octets,catalog_product_id) values(%s,%s,%s,%s,%s) "
        "on conflict do nothing",
        (seller, ids["integration"], PRODUCT_HASH, len(PRODUCT_REFERENCE), ids["product"]),
    )


@pytest.fixture(scope="module")
def worker() -> UUID:
    assert ADMIN_URL and RUNTIME_URL
    worker_id = uuid5(NAMESPACE_URL, f"{ISSUER}\0{WORKER_SUBJECT}")
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into identity_user(id,identity_issuer,identity_sub,"
            "email_normalized,email_verified) "
            "values(%s,%s,'dispute-owner','dispute.owner@example.test',true) "
            "on conflict do nothing",
            (OWNER, ISSUER),
        )
        _seed_seller(connection, SELLER)
        _seed_seller(connection, OFF_SELLER)
        # Só SELLER tem a regra ligada; OFF_SELLER prova o desligado.
        connection.execute(
            "insert into app_private.dispute_access_seller(seller_organization_id,actor,reason) "
            "values(%s,'pytest','teste da #542') on conflict do nothing",
            (SELLER,),
        )
        connection.commit()
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker_id, WORKER_CAPABILITY)
        connection.execute(
            "select app_private.ensure_fulfillment_worker(%s,%s,%s,"
            "'fulfillment-worker@system.appsell.internal',%s)",
            (worker_id, ISSUER, WORKER_SUBJECT, WORKER_LABEL),
        )
        connection.commit()
    return worker_id


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
    purchase: str,
    event_type: str,
    occurred_at: str,
    *,
    seller: UUID = SELLER,
    event_reference: str | None = None,
) -> tuple[UUID, bool]:
    assert RUNTIME_URL
    ids = _ids(seller)
    reference = event_reference or f"{RUN}:{purchase}:{event_type}:{occurred_at}"
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, INGEST_IDENTITY, INGEST_CAPABILITY)
        row = connection.execute(
            "select webhook_event_id,created from app_private.accept_checkout_webhook("
            "%s::uuid,%s::uuid,%s::text,%s::text,1::bigint,%s::text,7::smallint,"
            "%s::text,20::integer,%s::text,%s::text,11::smallint,%s::text,12::smallint,"
            "%s::timestamptz,%s::bigint,%s::uuid,%s::text,%s::text)",
            (
                seller,
                ids["endpoint"],
                _endpoint_hash(seller),
                KEY_REFERENCE,
                hashlib.sha256(reference.encode()).hexdigest(),
                hashlib.sha256(f"payload:{reference}".encode()).hexdigest(),
                event_type,
                PRODUCT_HASH,
                hashlib.sha256(f"{RUN}:{purchase}".encode()).hexdigest(),
                occurred_at,
                int(time.time()),
                uuid4(),
                f"buyer.{purchase}.{RUN}@example.test",
                None,
            ),
        ).fetchone()
        connection.commit()
    assert row is not None
    return cast(tuple[UUID, bool], row)


def _claim(worker: UUID) -> list[dict[str, object]]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, WORKER_CAPABILITY)
        claimed = connection.execute(
            "select app_private.claim_fulfillment_jobs(%s,%s,8,30)", (worker, WORKER_LABEL)
        ).fetchone()
        connection.commit()
    assert claimed is not None
    return cast(list[dict[str, object]], claimed[0])


def _apply(worker: UUID, job: dict[str, object]) -> dict[str, object]:
    assert RUNTIME_URL
    seller = UUID(str(job["seller_organization_id"]))
    with psycopg.connect(RUNTIME_URL) as connection:
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
                ISSUER,
            ),
        ).fetchone()
        connection.commit()
    assert applied is not None
    return cast(dict[str, object], applied[0])


def _drain(worker: UUID) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for _ in range(16):
        jobs = _claim(worker)
        if not jobs:
            return results
        results.extend(_apply(worker, job) for job in jobs)
    raise AssertionError("fulfillment queue did not drain")


def _send(
    worker: UUID,
    purchase: str,
    event_type: str,
    occurred_at: str,
    *,
    seller: UUID = SELLER,
    event_reference: str | None = None,
) -> None:
    _accept(purchase, event_type, occurred_at, seller=seller, event_reference=event_reference)
    for result in _drain(worker):
        assert result.get("status") == "completed", result


def _state(purchase: str, seller: UUID = SELLER) -> tuple[str | None, str | None]:
    """(status do grant da compra, status do acesso do par comprador×produto)."""
    assert ADMIN_URL
    purchase_sha = hashlib.sha256(f"{RUN}:{purchase}".encode()).hexdigest()
    with psycopg.connect(ADMIN_URL) as connection:
        grant = connection.execute(
            "select status,buyer_identity_user_id,catalog_product_id "
            "from app_private.entitlement_grant "
            "where seller_organization_id=%s and external_purchase_id_sha256=%s",
            (seller, purchase_sha),
        ).fetchone()
        if grant is None:
            return None, None
        access = connection.execute(
            "select status from buyer_entitlement where seller_organization_id=%s "
            "and buyer_identity_user_id=%s and catalog_product_id=%s",
            (seller, grant[1], grant[2]),
        ).fetchone()
    return cast(str, grant[0]), cast("str | None", access[0] if access else None)


T0 = "2026-11-01T10:00:00Z"
T1 = "2026-11-10T10:00:00Z"
T2 = "2026-11-20T10:00:00Z"
T3 = "2026-11-25T10:00:00Z"


def test_aberta_suspende_e_ganha_devolve_o_acesso(worker: UUID) -> None:
    # Pega: disputa aberta sem efeito, ou ganha que não devolve o acesso.
    _send(worker, "ganha", APPROVED, T0)
    assert _state("ganha") == ("active", "active")
    _send(worker, "ganha", OPENED, T1)
    assert _state("ganha") == ("suspended", "suspended")
    _send(worker, "ganha", WON, T2)
    assert _state("ganha") == ("active", "active")


def test_perdida_revoga_e_e_distinta_do_estorno(worker: UUID) -> None:
    # Pega: perdida virando "refunded" (a #558 mostraria "estornado").
    _send(worker, "perdida", APPROVED, T0)
    _send(worker, "perdida", OPENED, T1)
    _send(worker, "perdida", LOST, T2)
    assert _state("perdida") == ("charged_back", "revoked")


def test_perdida_e_final_nem_ganha_nem_aprovacao_repetida_devolvem(worker: UUID) -> None:
    # Pega: perdida desfeita por evento atrasado ou por purchase.approved reenviado.
    _send(worker, "final", APPROVED, T0)
    _send(worker, "final", LOST, T2)
    _send(worker, "final", WON, T3)
    _send(worker, "final", APPROVED, T3, event_reference=f"{RUN}:final:approved-replay")
    assert _state("final") == ("charged_back", "revoked")


def test_ganha_antes_da_aberta_mantem_o_acesso(worker: UUID) -> None:
    # Pega: a aberta atrasada suspendendo depois do desfecho.
    _send(worker, "ganha-antes", APPROVED, T0)
    _send(worker, "ganha-antes", WON, T2)
    _send(worker, "ganha-antes", OPENED, T1)
    assert _state("ganha-antes") == ("active", "active")


def test_perdida_antes_da_aberta_mantem_revogado(worker: UUID) -> None:
    # Pega: a aberta atrasada trocando "perdida" por "suspensa".
    _send(worker, "perdida-antes", APPROVED, T0)
    _send(worker, "perdida-antes", LOST, T2)
    _send(worker, "perdida-antes", OPENED, T1)
    assert _state("perdida-antes") == ("charged_back", "revoked")


def test_disputa_sobre_compra_estornada_nao_muda_nada(worker: UUID) -> None:
    # Pega: disputa ganha reativando uma compra já estornada.
    _send(worker, "estornada", APPROVED, T0)
    _send(worker, "estornada", REFUNDED, T1)
    _send(worker, "estornada", OPENED, T2)
    _send(worker, "estornada", WON, T3)
    assert _state("estornada") == ("refunded", "revoked")


def test_estorno_sobre_compra_suspensa_revoga(worker: UUID) -> None:
    # Pega: o estorno só revogava acesso 'active' e deixava o suspenso pendurado.
    _send(worker, "suspensa-estornada", APPROVED, T0)
    _send(worker, "suspensa-estornada", OPENED, T1)
    _send(worker, "suspensa-estornada", REFUNDED, T2)
    assert _state("suspensa-estornada") == ("refunded", "revoked")


def test_aprovacao_repetida_nao_reativa_compra_suspensa(worker: UUID) -> None:
    # Pega: o reenvio do purchase.approved (a Stripe reentrega por dias) liberando
    # o acesso de uma compra em disputa.
    _send(worker, "suspensa-replay", APPROVED, T0)
    _send(worker, "suspensa-replay", OPENED, T1)
    _send(worker, "suspensa-replay", APPROVED, T2, event_reference=f"{RUN}:suspensa:replay")
    assert _state("suspensa-replay") == ("suspended", "suspended")


def test_disputa_antes_da_aprovacao_nao_libera(worker: UUID) -> None:
    # Pega: a aprovação que chega depois da disputa criando acesso ativo.
    _send(worker, "sem-aprovacao", OPENED, T1)
    _send(worker, "sem-aprovacao", APPROVED, T0)
    grant, access = _state("sem-aprovacao")
    assert grant == "suspended"
    assert access != "active"


def test_evento_repetido_cria_um_job_so(worker: UUID) -> None:
    # Pega: a reentrega do Financial aplicando a mesma disputa duas vezes.
    _send(worker, "repetido", APPROVED, T0)
    reference = f"{RUN}:repetido:opened"
    first, created = _accept("repetido", OPENED, T1, event_reference=reference)
    again, created_again = _accept("repetido", OPENED, T1, event_reference=reference)
    assert created and not created_again and first == again
    _drain(worker)
    assert _state("repetido") == ("suspended", "suspended")


def test_retoma_de_onde_parou_quando_o_worker_cai(worker: UUID) -> None:
    # Pega: um worker que morre com o job em lease deixando a disputa sem efeito.
    assert ADMIN_URL
    _send(worker, "retoma", APPROVED, T0)
    _accept("retoma", OPENED, T1)
    jobs = _claim(worker)
    assert len(jobs) == 1
    # O processo cai aqui: nada aplicado, o lease fica preso até expirar.
    assert _state("retoma") == ("active", "active")
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update app_private.fulfillment_job set lease_expires_at=clock_timestamp()"
            "-interval '1 second' where seller_organization_id=%s and id=%s",
            (SELLER, UUID(str(jobs[0]["id"]))),
        )
        connection.commit()
    # O job velho perdeu o lease: o apply dele é recusado.
    with pytest.raises(psycopg.errors.SerializationFailure):
        _apply(worker, jobs[0])
    results = _drain(worker)
    assert [result["reason"] for result in results] == ["grant_suspended"]
    assert _state("retoma") == ("suspended", "suspended")


def test_loja_desligada_aceita_o_evento_e_nao_muda_o_acesso(worker: UUID) -> None:
    # Pega: a regra valendo para loja que não foi ligada.
    _send(worker, "desligada", APPROVED, T0, seller=OFF_SELLER)
    _accept("desligada", OPENED, T1, seller=OFF_SELLER)
    results = _drain(worker)
    assert [result["reason"] for result in results] == ["dispute_access_disabled"]
    assert _state("desligada", OFF_SELLER) == ("active", "active")


def test_runtime_nao_le_nem_escreve_a_chave_por_loja() -> None:
    # Pega: a api ligando a regra para uma loja sem passar pela operação.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("select 1 from app_private.dispute_access_seller")


def test_runtime_nao_chama_a_funcao_de_disputa_direto(worker: UUID) -> None:
    # Pega: a regra aplicada sem o lease e os locks da função de fulfillment.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, worker, WORKER_CAPABILITY, SELLER)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "select app_private.apply_checkout_dispute(%s,%s,%s,%s,%s,%s,%s,"
                "%s::timestamptz,%s,%s,%s)",
                (
                    worker,
                    SELLER,
                    uuid4(),
                    uuid4(),
                    OPENED,
                    _ids(SELLER)["integration"],
                    "0" * 64,
                    T1,
                    uuid4(),
                    uuid4(),
                    _ids(SELLER)["product"],
                ),
            )
