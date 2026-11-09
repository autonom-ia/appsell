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
# Ligadas e depois desligadas (ou religadas) dentro do próprio teste.
TOGGLE_SELLER = UUID("e5420000-0000-4000-8000-000000000030")
RELINK_SELLER = UUID("e5420000-0000-4000-8000-000000000040")
# Mesma compra e mesma disputa que SELLER, em outra loja: isolamento.
OTHER_SELLER = UUID("e5420000-0000-4000-8000-000000000050")
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
DISPUTES = frozenset({OPENED, WON, LOST})
# A loja SELLER foi ligada nesta data: vale a disputa aberta depois dela.
ENABLED_AT = "2026-01-01T00:00:00Z"
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
        for extra in (TOGGLE_SELLER, RELINK_SELLER, OTHER_SELLER):
            _seed_seller(connection, extra)
            connection.execute(
                "insert into app_private.dispute_access_switch("
                "seller_organization_id,action,actor,reason,created_at) "
                "values(%s,'enable','pytest','teste da #542',%s)",
                (extra, ENABLED_AT),
            )
        # Só SELLER tem a regra ligada. OFF_SELLER foi ligada e depois
        # desligada: prova o desligado e que desligar não apaga quem ligou.
        connection.execute(
            "insert into app_private.dispute_access_switch("
            "seller_organization_id,action,actor,reason,"
            "created_at) values(%s,'enable','pytest','teste da #542',%s),"
            "(%s,'enable','pytest','teste da #542',clock_timestamp()),"
            "(%s,'disable','pytest','teste da #542',clock_timestamp())",
            (SELLER, ENABLED_AT, OFF_SELLER, OFF_SELLER),
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


_ACCEPT_SQL = (
    "select webhook_event_id,created from app_private.accept_checkout_webhook("
    "%s::uuid,%s::uuid,%s::text,%s::text,1::bigint,%s::text,7::smallint,"
    "%s::text,20::integer,%s::text,%s::text,11::smallint,%s::text,12::smallint,"
    "%s::timestamptz,%s::bigint,%s::uuid,%s::text,%s::text)"
)
_ACCEPT_DISPUTE_SQL = (
    "select webhook_event_id,created from app_private.accept_checkout_dispute_webhook("
    "%s::uuid,%s::uuid,%s::text,%s::text,1::bigint,%s::text,7::smallint,"
    "%s::text,20::integer,%s::text,%s::text,11::smallint,%s::text,12::smallint,"
    "%s::timestamptz,%s::bigint,%s::uuid,%s::text,%s::text,%s::text,%s::timestamptz)"
)


def _dispute_sha(purchase: str, dispute: str) -> str:
    return hashlib.sha256(f"{RUN}:{purchase}:{dispute}".encode()).hexdigest()


def _accept(
    purchase: str,
    event_type: str,
    occurred_at: str,
    *,
    seller: UUID = SELLER,
    event_reference: str | None = None,
    dispute: str = "dp_A",
    created_at: str | None = "2026-11-10T10:00:00Z",
    buyer: str | None = None,
    with_buyer: bool = True,
    without_details: bool = False,
) -> tuple[UUID, bool]:
    """Aceita pelo ingresso real. Disputa vai pela aceitação de disputa, com o
    id e a data real da disputa; `without_details` simula a aceitação sem eles."""
    assert RUNTIME_URL
    ids = _ids(seller)
    reference = event_reference or f"{RUN}:{purchase}:{event_type}:{dispute}:{occurred_at}"
    is_dispute = event_type in DISPUTES and not without_details
    email = f"buyer.{buyer or purchase}.{RUN}@example.test" if with_buyer else None
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, INGEST_IDENTITY, INGEST_CAPABILITY)
        row = connection.execute(
            _ACCEPT_DISPUTE_SQL if is_dispute else _ACCEPT_SQL,
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
                email,
                None,
                *((_dispute_sha(purchase, dispute), created_at) if is_dispute else ()),
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
    dispute: str = "dp_A",
    created_at: str | None = "2026-11-10T10:00:00Z",
    buyer: str | None = None,
) -> list[dict[str, object]]:
    _accept(
        purchase,
        event_type,
        occurred_at,
        seller=seller,
        event_reference=event_reference,
        dispute=dispute,
        created_at=created_at,
        buyer=buyer,
    )
    results = _drain(worker)
    for result in results:
        assert result.get("status") == "completed", result
    return results


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
    assert _skip_reason("final") == "purchase_charged_back"


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


def test_aberta_antes_de_ligar_e_perdida_depois_nao_muda_o_acesso(worker: UUID) -> None:
    # Pega: o desfecho que chega depois de a loja ser ligada fazendo valer uma
    # disputa aberta antes dela (o piso é a data da disputa, não a do evento).
    antiga = "2025-12-01T10:00:00Z"
    _send(worker, "antes-depois", APPROVED, T0)
    _send(worker, "antes-depois", OPENED, antiga, dispute="dp_OLD", created_at=antiga)
    results = _send(worker, "antes-depois", LOST, T2, dispute="dp_OLD", created_at=antiga)
    assert [result["reason"] for result in results] == ["dispute_before_enable"]
    assert _state("antes-depois") == ("active", "active")


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
    # Pega: a aprovação pulada registrada como "purchase_refunded" no audit.
    assert _skip_reason("suspensa-replay") == "purchase_disputed"


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
    event_id, _created = _accept("desligada", OPENED, T1, seller=OFF_SELLER)
    results = _drain(worker)
    assert [result["reason"] for result in results] == ["dispute_access_disabled"]
    assert _state("desligada", OFF_SELLER) == ("active", "active")
    # Gate #147: a disputa que a loja desligada ignorou aparece na conciliação,
    # em vez de deixar só um audit_event.
    assert any(
        row[0] == "dispute_while_switch_off" and row[2] == event_id for row in _report(OFF_SELLER)
    )


def test_disputa_sem_data_e_gravada_sem_efeito_e_alerta(worker: UUID) -> None:
    # Pega: a data ausente caindo para "agora" e fazendo valer disputa antiga;
    # ou a disputa sem data sumindo sem ninguém saber.
    _send(worker, "sem-data", APPROVED, T0)
    event_id, created = _accept("sem-data", LOST, T2, dispute="dp_SEMDATA", created_at=None)
    assert created
    results = _drain(worker)
    assert [result["reason"] for result in results] == ["dispute_without_created_at"]
    assert _state("sem-data") == ("active", "active")
    assert any(row[0] == "dispute_without_created_at" and row[2] == event_id for row in _report())


def test_outra_disputa_aberta_mantem_suspenso_quando_a_primeira_ganha(worker: UUID) -> None:
    # Pega: o estado único por compra (A aberta, B aberta, A ganha devolvia o
    # acesso com B ainda aberta).
    _send(worker, "duas", APPROVED, T0)
    _send(worker, "duas", OPENED, T1, dispute="dp_A", created_at=T1)
    _send(worker, "duas", OPENED, T2, dispute="dp_B", created_at=T2)
    _send(worker, "duas", WON, T3, dispute="dp_A", created_at=T1)
    assert _state("duas") == ("suspended", "suspended")
    _send(worker, "duas", WON, T3, dispute="dp_B", created_at=T2)
    assert _state("duas") == ("active", "active")


def test_uma_perdida_entre_duas_disputas_e_final(worker: UUID) -> None:
    # Pega: a vitória de outra disputa desfazendo a perdida.
    _send(worker, "duas-perdida", APPROVED, T0)
    _send(worker, "duas-perdida", OPENED, T1, dispute="dp_A", created_at=T1)
    _send(worker, "duas-perdida", OPENED, T2, dispute="dp_B", created_at=T2)
    _send(worker, "duas-perdida", LOST, T3, dispute="dp_A", created_at=T1)
    _send(worker, "duas-perdida", WON, T3, dispute="dp_B", created_at=T2)
    assert _state("duas-perdida") == ("charged_back", "revoked")


def test_aberta_reenviada_com_data_mais_nova_nao_desfaz_o_desfecho(worker: UUID) -> None:
    # Pega: o retry do Financial muda o occurred_at; a aberta reenviada depois
    # do desfecho, com data mais nova, deixava a compra suspensa para sempre.
    _send(worker, "retry-aberta", APPROVED, T0)
    _send(worker, "retry-aberta", WON, T2)
    _send(worker, "retry-aberta", OPENED, T3)
    assert _state("retry-aberta") == ("active", "active")


def test_disputa_aberta_antes_de_ligar_a_loja_fica_sem_efeito(worker: UUID) -> None:
    # Pega: o primeiro deliver do Financial relendo disputas antigas e
    # revogando hoje o acesso de uma perda de meses atrás.
    antiga = "2025-12-01T10:00:00Z"
    _send(worker, "antiga", APPROVED, T0)
    results = _send(worker, "antiga", LOST, T2, dispute="dp_OLD", created_at=antiga)
    assert [result["reason"] for result in results] == ["dispute_before_enable"]
    _send(worker, "antiga", OPENED, T2, dispute="dp_OLD2", created_at=antiga)
    assert _state("antiga") == ("active", "active")


def test_abertura_aprovacao_e_vitoria_criam_o_acesso(worker: UUID) -> None:
    # Pega: o grant que nasce suspenso pula a aprovação, e a vitória o ativava
    # sem nunca criar o acesso.
    _send(worker, "sem-acesso", OPENED, T1)
    _send(worker, "sem-acesso", APPROVED, T0)
    assert _state("sem-acesso") == ("suspended", None)
    _send(worker, "sem-acesso", WON, T2)
    assert _state("sem-acesso") == ("active", "active")


def test_vitoria_sem_aprovacao_nao_cria_o_acesso(worker: UUID) -> None:
    # Pega: a disputa sozinha liberando acesso de compra que nunca foi aprovada.
    _send(worker, "so-disputa", OPENED, T1)
    _send(worker, "so-disputa", WON, T2)
    assert _state("so-disputa") == ("active", None)
    _send(worker, "so-disputa", APPROVED, T0)
    assert _state("so-disputa") == ("active", "active")


def test_estorno_de_uma_compra_com_outra_suspensa_suspende_o_acesso(worker: UUID) -> None:
    # Pega: A estornada e B suspensa (mesmo produto) revogando o acesso, e a
    # vitória de B não o devolvendo.
    _send(worker, "par-a", APPROVED, T0, buyer="par")
    _send(worker, "par-b", APPROVED, T0, buyer="par")
    _send(worker, "par-b", OPENED, T1, buyer="par")
    assert _state("par-b") == ("suspended", "active")
    _send(worker, "par-a", REFUNDED, T2, buyer="par")
    assert _state("par-a") == ("refunded", "suspended")
    _send(worker, "par-b", WON, T3, buyer="par")
    assert _state("par-b") == ("active", "active")


def test_estorno_nao_troca_a_perdida_por_estorno(worker: UUID) -> None:
    # Pega: o refunded sobrescrevendo o charged_back (a #558 mostraria
    # "estornado" para dinheiro que saiu pela disputa).
    _send(worker, "perdida-estorno", APPROVED, T0)
    _send(worker, "perdida-estorno", LOST, T2)
    _send(worker, "perdida-estorno", REFUNDED, T3)
    assert _state("perdida-estorno") == ("charged_back", "revoked")


def test_disputa_sem_os_dados_da_disputa_nao_e_aplicada(worker: UUID) -> None:
    # Pega: um evento de disputa aceito pelo caminho antigo, sem id nem data,
    # aplicado como se fosse a disputa de sempre.
    assert ADMIN_URL
    _send(worker, "sem-dados", APPROVED, T0)
    _accept("sem-dados", OPENED, T1, without_details=True)
    jobs = _claim(worker)
    assert len(jobs) == 1
    try:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            _apply(worker, jobs[0])
        assert _state("sem-dados") == ("active", "active")
    finally:
        with psycopg.connect(ADMIN_URL) as connection:
            connection.execute(
                "update app_private.fulfillment_job set status='dead_letter',"
                "lease_owner=null,lease_expires_at=null,error_code='apply_retryable' "
                "where seller_organization_id=%s and id=%s",
                (SELLER, UUID(str(jobs[0]["id"]))),
            )
            connection.commit()


def test_reentrega_com_outra_disputa_no_mesmo_evento_e_recusada(worker: UUID) -> None:
    # Pega: o mesmo id de evento gravando o estado de outra disputa.
    reference = f"{RUN}:reentrega:opened"
    _accept("reentrega", OPENED, T1, event_reference=reference, dispute="dp_A")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _accept("reentrega", OPENED, T1, event_reference=reference, dispute="dp_B")
    _drain(worker)


def _report(seller: UUID = SELLER) -> list[tuple[str, str | None, UUID | None]]:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        rows = connection.execute(
            "select category,external_purchase_id_sha256,reference_id "
            "from app_private.checkout_reconciliation_report("
            "now()-interval '1 day',now()+interval '1 minute',interval '1 hour') "
            "where seller_organization_id=%s",
            (seller,),
        ).fetchall()
    return cast(list[tuple[str, str | None, UUID | None]], rows)


def _skip_reason(purchase: str, seller: UUID = SELLER) -> str | None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select metadata->>'reason' from public.audit_event "
            "where seller_organization_id=%s and action='commerce.fulfillment_skipped' "
            "and metadata->>'external_purchase_id_sha256'=%s "
            "order by created_at desc limit 1",
            (seller, _sha(purchase)),
        ).fetchone()
    return None if row is None else cast(str | None, row[0])


def _switch(seller: UUID, action: str, created_at: str) -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "insert into app_private.dispute_access_switch("
            "seller_organization_id,action,actor,reason,created_at) "
            "values(%s,%s,'pytest','teste da #542',%s)",
            (seller, action, created_at),
        )
        connection.commit()


def _sha(purchase: str) -> str:
    return hashlib.sha256(f"{RUN}:{purchase}".encode()).hexdigest()


def test_desfecho_de_disputa_que_vale_e_aplicado_com_a_loja_desligada(worker: UUID) -> None:
    # Pega: loja desligada descartando a vitória de uma disputa que já
    # suspendeu o acesso; quem ganhou ficaria suspenso para sempre, sem alerta.
    _send(worker, "desligou", APPROVED, T0, seller=TOGGLE_SELLER)
    _send(worker, "desligou", OPENED, T1, seller=TOGGLE_SELLER)
    assert _state("desligou", TOGGLE_SELLER) == ("suspended", "suspended")
    _switch(TOGGLE_SELLER, "disable", "2026-01-02T00:00:00Z")
    report = _report(TOGGLE_SELLER)
    assert ("disputed_while_disabled", _sha("desligou"), None) in report
    # Disputa nova com a loja desligada: ignorada.
    results = _send(worker, "desligou", OPENED, T2, seller=TOGGLE_SELLER, dispute="dp_NOVA")
    assert [result["reason"] for result in results] == ["dispute_access_disabled"]
    results = _send(worker, "desligou", WON, T3, seller=TOGGLE_SELLER)
    assert [result["reason"] for result in results] == ["grant_active"]
    assert _state("desligou", TOGGLE_SELLER) == ("active", "active")
    assert ("disputed_while_disabled", _sha("desligou"), None) not in _report(TOGGLE_SELLER)


def test_religar_nao_tira_o_efeito_da_disputa_que_ja_valia(worker: UUID) -> None:
    # Pega: o piso novo do religamento anulando uma disputa que já suspendeu,
    # e a vitória dela não devolvendo o acesso.
    _send(worker, "religou", APPROVED, T0, seller=RELINK_SELLER)
    _send(worker, "religou", OPENED, T1, seller=RELINK_SELLER)
    _switch(RELINK_SELLER, "disable", "2026-01-02T00:00:00Z")
    _switch(RELINK_SELLER, "enable", "2026-12-01T00:00:00Z")
    _send(worker, "religou", WON, T3, seller=RELINK_SELLER)
    assert _state("religou", RELINK_SELLER) == ("active", "active")


def test_disputa_de_uma_loja_nao_toca_a_mesma_compra_em_outra(worker: UUID) -> None:
    # Pega: a purchase_dispute ou o recálculo cruzando lojas com a mesma
    # referência de compra e o mesmo id de disputa.
    _send(worker, "cruzada", APPROVED, T0)
    _send(worker, "cruzada", APPROVED, T0, seller=OTHER_SELLER)
    _send(worker, "cruzada", OPENED, T1)
    assert _state("cruzada") == ("suspended", "suspended")
    assert _state("cruzada", OTHER_SELLER) == ("active", "active")
    _send(worker, "cruzada", LOST, T2, seller=OTHER_SELLER)
    assert _state("cruzada") == ("suspended", "suspended")
    assert _state("cruzada", OTHER_SELLER) == ("charged_back", "revoked")


def _access_id(purchase: str) -> UUID:
    assert ADMIN_URL
    purchase_sha = hashlib.sha256(f"{RUN}:{purchase}".encode()).hexdigest()
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select access.id from app_private.entitlement_grant grant_row "
            "join buyer_entitlement access on access.seller_organization_id="
            "grant_row.seller_organization_id and access.buyer_identity_user_id="
            "grant_row.buyer_identity_user_id and access.catalog_product_id="
            "grant_row.catalog_product_id "
            "where grant_row.seller_organization_id=%s "
            "and grant_row.external_purchase_id_sha256=%s",
            (SELLER, purchase_sha),
        ).fetchone()
    assert row is not None
    return cast(UUID, row[0])


def _force_access(access_id: UUID, status: str) -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "update buyer_entitlement set status=%s where seller_organization_id=%s and id=%s",
            (status, SELLER, access_id),
        )
        connection.commit()


def test_conciliacao_ve_compra_em_disputa_com_acesso_ativo(worker: UUID) -> None:
    # Pega: a conciliação olhando só 'refunded' e não vendo o suspenso ou
    # perdido com o acesso ainda liberado.
    _send(worker, "concilia-suspensa", APPROVED, T0)
    _send(worker, "concilia-suspensa", OPENED, T1)
    access = _access_id("concilia-suspensa")
    purchase_sha = hashlib.sha256(f"{RUN}:concilia-suspensa".encode()).hexdigest()
    _force_access(access, "active")
    try:
        assert ("disputed_with_access", purchase_sha, access) in _report()
    finally:
        _force_access(access, "suspended")
    assert ("disputed_with_access", purchase_sha, access) not in _report()


def test_conciliacao_ve_acesso_suspenso_sem_disputa(worker: UUID) -> None:
    # Pega: uma suspensão que perdeu o motivo e ficaria para sempre.
    _send(worker, "concilia-sem-motivo", APPROVED, T0)
    access = _access_id("concilia-sem-motivo")
    _force_access(access, "suspended")
    try:
        assert ("suspended_without_dispute", None, access) in _report()
    finally:
        _force_access(access, "active")


def test_conciliacao_separa_a_disputa_sem_job_do_estorno(worker: UUID) -> None:
    # Pega: o evento de disputa sem job caindo em refund_without_fulfillment.
    event_id, created = _accept("concilia-sem-job", LOST, T2, with_buyer=False)
    assert created
    report = _report()
    assert any(row[0] == "dispute_without_fulfillment" and row[2] == event_id for row in report)
    assert not any(row[0] == "refund_without_fulfillment" and row[2] == event_id for row in report)


def test_tabelas_novas_tem_rls_forcada_e_o_runtime_nao_le(worker: UUID) -> None:
    # Pega: tabela nova sem RLS (o §7 exige enable + force) ou legível pela api.
    assert ADMIN_URL and RUNTIME_URL
    with psycopg.connect(ADMIN_URL) as connection:
        flags = connection.execute(
            "select relname, relrowsecurity, relforcerowsecurity from pg_class "
            "where oid in ('app_private.dispute_access_switch'::regclass,"
            "'app_private.checkout_dispute_event'::regclass,"
            "'app_private.purchase_dispute'::regclass) order by 1"
        ).fetchall()
        ready = connection.execute("select app_private.schema_ready()").fetchone()
    assert flags == [
        ("checkout_dispute_event", True, True),
        ("dispute_access_switch", True, True),
        ("purchase_dispute", True, True),
    ]
    assert ready == (True,)
    for tabela in ("app_private.checkout_dispute_event", "app_private.purchase_dispute"):
        with psycopg.connect(RUNTIME_URL) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(f"select 1 from {tabela}")  # noqa: S608 - nome literal


def test_runtime_nao_le_nem_escreve_a_chave_por_loja() -> None:
    # Pega: a api ligando a regra para uma loja sem passar pela operação.
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("select 1 from app_private.dispute_access_switch")


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


def test_so_a_funcao_de_disputa_le_a_chave(worker: UUID) -> None:
    # Pega: outra função (ou um grant) passando a decidir pela chave por loja.
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        leitoras = connection.execute(
            "select proc.proname from pg_proc proc "
            "join pg_namespace ns on ns.oid=proc.pronamespace "
            "where ns.nspname not in ('pg_catalog','information_schema') "
            "and position('dispute_access_switch' in proc.prosrc) > 0 order by 1"
        ).fetchall()
        privilegios = connection.execute(
            "select papel, has_table_privilege(papel,'app_private.dispute_access_switch',acao) "
            "from unnest(array['appsell_runtime','appsell_reconciler','public']) papel, "
            "unnest(array['select','insert','update','delete']) acao"
        ).fetchall()
    # A trigger só recusa; ela cita a tabela na mensagem, não a lê. O
    # readiness só confere que a tabela existe e tem RLS. A conciliação só lê,
    # para o alerta de compra suspensa em loja desligada.
    assert [nome for (nome,) in leitoras] == [
        "apply_checkout_dispute",
        "checkout_reconciliation_report",
        "dispute_access_switch_append_only",
        "schema_ready_base_0016",
    ]
    assert not any(pode for _papel, pode in privilegios)


def test_a_chave_guarda_quem_ligou_e_desligou(worker: UUID) -> None:
    # Pega: desligar uma loja apagando a auditoria de quem ligou.
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        historico = connection.execute(
            "select action,actor from app_private.dispute_access_switch "
            "where seller_organization_id=%s order by created_at,id",
            (OFF_SELLER,),
        ).fetchall()
        assert historico[-2:] == [("enable", "pytest"), ("disable", "pytest")]
        for comando in (
            "delete from app_private.dispute_access_switch where seller_organization_id=%s",
            "update app_private.dispute_access_switch set actor='outro' "
            "where seller_organization_id=%s",
        ):
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                connection.execute(comando, (OFF_SELLER,))
            connection.rollback()
