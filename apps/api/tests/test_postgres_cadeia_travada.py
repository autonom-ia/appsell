"""A leitura da cadeia travada num Postgres de verdade (#550, migration 0071).

Os dados entram pelo superusuário e a leitura sai pelo papel
`appsell_reconciler`, que é o caminho real do checador do host. O banco de
teste é compartilhado com outras suítes, então o teste mede a **diferença**
que os próprios dados produzem, não o total.

Cada caso que deve aparecer tem um vizinho que não deve: o e-mail recente que
ainda não está parado, o grant recente que ainda pode ganhar e-mail, o grant
antigo de antes da janela, o grant cujo e-mail já saiu, o `dead_letter` antigo
e o descadastro.

A janela é lida de dois jeitos: a larga, de dois dias, e a que o checador
manda a cada 5 minutos (`since = now - 10 min`). A segunda é a que roda em
produção, e foi nela que o grant sem e-mail sumia.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

ADMIN_URL = os.getenv("APPSELL_TEST_POSTGRES_ADMIN_URL")
RECONCILER_URL = os.getenv("APPSELL_TEST_POSTGRES_RECONCILER_URL")

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RECONCILER_URL)),
    reason="isolated PostgreSQL environment is required",
)

RUN = uuid4().hex[:8]
ISSUER = "https://identity.test.autonomia.site"
NOW = datetime.now(UTC)
SINCE = NOW - timedelta(days=2)
# O que o checador manda: o fim da rodada anterior menos a sobreposição.
SINCE_DO_CHECADOR = NOW - timedelta(minutes=10)
EMAIL_STALE = timedelta(minutes=30)
GRANT_STALE = timedelta(minutes=15)

Connection = psycopg.Connection[tuple[object, ...]]
Leitura = dict[str, tuple[int, datetime | None]]


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self) -> None:
        self.slug = f"chain-{RUN}"
        self.seller = uuid4()
        self.integration = uuid4()
        self.endpoint = uuid4()
        self.product = uuid4()

    def purchase(self, name: str) -> str:
        return _sha(f"{self.slug}:{name}")


def _buyer(connection: Connection, name: str) -> UUID:
    buyer = uuid4()
    connection.execute(
        "insert into identity_user(id,identity_issuer,identity_sub,email_normalized,"
        "email_verified) values(%s,%s,%s,%s,true)",
        (buyer, ISSUER, f"chain-{name}-{RUN}", f"chain.{name}.{RUN}@example.test"),
    )
    return buyer


def _seed_store(connection: Connection, store: Store) -> None:
    connection.execute(
        "insert into seller_organization(id,slug,display_name,status) values(%s,%s,%s,'active')",
        (store.seller, store.slug, "Chain"),
    )
    connection.execute(
        "insert into catalog_product(seller_organization_id,id,slug,title,description,status)"
        " values(%s,%s,'curso','Curso','Curso','draft')",
        (store.seller, store.product),
    )
    connection.execute(
        "insert into checkout_integration(seller_organization_id,id,adapter,display_name,"
        "status) values(%s,%s,'generic_hmac_v1','Chain checkout','active')",
        (store.seller, store.integration),
    )
    connection.execute(
        "insert into webhook_endpoint(seller_organization_id,id,checkout_integration_id,"
        "endpoint_key_sha256,secret_reference,status) values(%s,%s,%s,%s,%s,'active')",
        (store.seller, store.endpoint, store.integration, _sha(store.slug), f"chain-{RUN}"),
    )


def _sale(connection: Connection, store: Store, name: str, received_at: datetime) -> None:
    connection.execute(
        "insert into webhook_event(seller_organization_id,checkout_integration_id,"
        "webhook_endpoint_id,external_event_id_sha256,external_event_id_octets,"
        "payload_sha256,payload_octets,event_type,external_product_id_sha256,"
        "external_product_id_octets,external_purchase_id_sha256,external_purchase_id_octets,"
        "occurred_at,signed_at,received_at) "
        "values(%s,%s,%s,%s,10,%s,100,'purchase.approved',%s,10,%s,10,%s,%s,%s)",
        (
            store.seller,
            store.integration,
            store.endpoint,
            _sha(f"{store.slug}:event:{name}"),
            _sha(f"{store.slug}:payload:{name}"),
            _sha(f"{store.slug}:product"),
            store.purchase(name),
            received_at,
            received_at,
            received_at,
        ),
    )


def _grant(connection: Connection, store: Store, name: str, created_at: datetime) -> UUID:
    buyer = _buyer(connection, name)
    connection.execute(
        "insert into app_private.entitlement_grant(seller_organization_id,"
        "checkout_integration_id,external_purchase_id_sha256,buyer_identity_user_id,"
        "catalog_product_id,status,occurred_at,created_at,updated_at)"
        " values(%s,%s,%s,%s,%s,'active',%s,%s,%s)",
        (
            store.seller,
            store.integration,
            store.purchase(name),
            buyer,
            store.product,
            created_at,
            created_at,
            created_at,
        ),
    )
    return buyer


def _email(
    connection: Connection,
    store: Store,
    name: str,
    status: str,
    created_at: datetime,
    *,
    recipient: UUID | None = None,
    sent_at: datetime | None = None,
    updated_at: datetime | None = None,
    error_code: str | None = None,
) -> None:
    destinatario = recipient or _buyer(connection, f"mail-{name}")
    connection.execute(
        "insert into app_private.email_outbox(seller_organization_id,kind,dedupe_key,"
        "recipient_identity_user_id,recipient_email,payload,status,sent_at,error_code,"
        "created_at,updated_at) values(%s,'purchase_confirmed',%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            store.seller,
            f"purchase:{store.integration}:{store.purchase(name)}",
            destinatario,
            f"chain.mail.{name}.{RUN}@example.test",
            Jsonb({"schema_version": 1}),
            status,
            sent_at,
            error_code,
            created_at,
            updated_at or (NOW if status == "dead_letter" else created_at),
        ),
    )


def _read(
    since: datetime = SINCE,
    email_stale: timedelta = EMAIL_STALE,
    grant_stale: timedelta = GRANT_STALE,
) -> Leitura:
    assert RECONCILER_URL
    with psycopg.connect(RECONCILER_URL) as connection:
        rows = connection.execute(
            "select metric, quantity, observed_at from app_private.chain_health_report(%s,%s,%s)",
            (since, email_stale, grant_stale),
        ).fetchall()
    return {str(metric): (int(quantity), observed) for metric, quantity, observed in rows}


Leituras = dict[str, tuple[Leitura, Leitura]]


@pytest.fixture(scope="module")
def leituras() -> Leituras:
    assert ADMIN_URL
    larga, checador = _read(), _read(SINCE_DO_CHECADOR)
    store = Store()
    with psycopg.connect(ADMIN_URL) as connection:
        _seed_store(connection, store)
        # E-mail: dois parados, um recente que ainda não está, um enviado e um morto.
        _email(connection, store, "parado-1", "pending", NOW - timedelta(hours=2))
        _email(connection, store, "parado-2", "retry", NOW - timedelta(hours=3))
        _email(connection, store, "recente", "pending", NOW - timedelta(minutes=1))
        _email(
            connection,
            store,
            "enviado",
            "sent",
            NOW - timedelta(days=1),
            sent_at=NOW - timedelta(days=1),
        )
        _email(connection, store, "morto", "dead_letter", NOW - timedelta(hours=5))
        # Fora do alerta: o dead_letter antigo já alertou, e o descadastro é escolha.
        _email(
            connection,
            store,
            "morto-antigo",
            "dead_letter",
            NOW - timedelta(days=3),
            updated_at=NOW - timedelta(days=3),
        )
        _email(
            connection,
            store,
            "descadastro",
            "dead_letter",
            NOW - timedelta(hours=1),
            error_code="suppressed",
        )
        # Grant: sem e-mail e com e-mail que não saiu contam; os vizinhos não.
        _grant(connection, store, "sem-email", NOW - timedelta(hours=1))
        # Na janela do checador: 20 min, já passou dos 15 do limite.
        _grant(connection, store, "sem-email-agora", NOW - timedelta(minutes=20))
        nao_saiu = _grant(connection, store, "email-nao-saiu", NOW - timedelta(hours=1))
        _email(
            connection,
            store,
            "email-nao-saiu",
            "retry",
            NOW - timedelta(hours=1),
            recipient=nao_saiu,
        )
        com_email = _grant(connection, store, "com-email", NOW - timedelta(hours=1))
        _email(
            connection,
            store,
            "com-email",
            "sent",
            NOW - timedelta(hours=1),
            recipient=com_email,
            sent_at=NOW - timedelta(hours=1),
        )
        _grant(connection, store, "fresco", NOW - timedelta(minutes=1))
        _grant(connection, store, "antigo", NOW - timedelta(days=40))
        # Vendas: uma na semana, uma fora.
        _sale(connection, store, "semana", NOW - timedelta(days=1))
        _sale(connection, store, "velha", NOW - timedelta(days=10))
        connection.commit()
    return {"larga": (larga, _read()), "checador": (checador, _read(SINCE_DO_CHECADOR))}


@pytest.fixture(scope="module")
def delta(leituras: Leituras) -> tuple[Leitura, Leitura]:
    return leituras["larga"]


def _diferenca(delta: tuple[Leitura, Leitura], metrica: str) -> int:
    antes, depois = delta
    return depois[metrica][0] - antes[metrica][0]


def test_devolve_as_seis_metricas() -> None:
    assert set(_read()) == {
        "email_stalled",
        "email_dead_letter",
        "grant_without_email",
        "week_sales",
        "week_grants",
        "week_emails_sent",
    }


def test_email_parado_conta_so_o_que_passou_do_limite(delta: tuple[Leitura, Leitura]) -> None:
    # Os dois parados e o "não saiu" (retry há 1 h); o recente fica fora.
    assert _diferenca(delta, "email_stalled") == 3
    observado = delta[1]["email_stalled"][1]
    assert observado is not None and observado <= NOW - EMAIL_STALE


def test_dead_letter_novo_aparece_pelo_instante(delta: tuple[Leitura, Leitura]) -> None:
    # Só o "morto": o antigo está fora da janela e o descadastro não é falha.
    assert _diferenca(delta, "email_dead_letter") == 1
    observado = delta[1]["email_dead_letter"][1]
    assert observado is not None and observado >= NOW - timedelta(seconds=1)


def test_grant_sem_email_respeita_janela_e_limite(delta: tuple[Leitura, Leitura]) -> None:
    # Sem e-mail (1 h e 20 min) e com e-mail que não saiu; o enviado, o fresco
    # e o antigo ficam de fora.
    assert _diferenca(delta, "grant_without_email") == 3


def test_grant_sem_email_aparece_na_janela_do_checador(leituras: Leituras) -> None:
    # A rodada de 5 min manda since = now - 10 min. A janela anda deslocada pelo
    # limite: [since - 15 min, now - 15 min) pega o grant de 20 min.
    antes, depois = leituras["checador"]
    assert depois["grant_without_email"][0] - antes["grant_without_email"][0] == 1
    observado = depois["grant_without_email"][1]
    assert observado is not None and observado <= NOW - GRANT_STALE


def test_resumo_da_semana(delta: tuple[Leitura, Leitura]) -> None:
    assert _diferenca(delta, "week_sales") == 1
    # Os cinco grants dos últimos 7 dias; o de 40 dias fica fora.
    assert _diferenca(delta, "week_grants") == 5
    assert _diferenca(delta, "week_emails_sent") == 2


@pytest.mark.parametrize(
    ("since", "email_stale", "grant_stale"),
    [
        (NOW - timedelta(days=40), EMAIL_STALE, GRANT_STALE),
        (NOW + timedelta(hours=1), EMAIL_STALE, GRANT_STALE),
        (SINCE, timedelta(seconds=10), GRANT_STALE),
        (SINCE, EMAIL_STALE, timedelta(days=2)),
    ],
)
def test_janela_invalida_e_recusada(
    since: datetime, email_stale: timedelta, grant_stale: timedelta
) -> None:
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _read(since, email_stale, grant_stale)


def test_o_papel_nao_le_a_tabela_direto() -> None:
    assert RECONCILER_URL
    with (
        psycopg.connect(RECONCILER_URL) as connection,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        connection.execute("select count(*) from app_private.email_outbox")
