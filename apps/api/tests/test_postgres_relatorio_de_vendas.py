from __future__ import annotations

import hashlib
import hmac
import os
import time
from datetime import date
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest

# Relatório de vendas por período e produto (migration 0072, #562). Prova:
# - conta a compra uma vez, pela primeira aprovação, e o estorno pelo primeiro
#   evento de estorno, no fuso de São Paulo;
# - replay do mesmo evento não conta de novo (idempotência da ingestão);
# - loja A não vê a B, e só dono e admin leem;
# - período máximo, limite de taxa e auditoria do export;
# - nada de e-mail, identidade ou payload na saída.

ADMIN_URL = os.getenv("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.getenv("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY = bytes.fromhex(os.getenv("APPSELL_TEST_CONTEXT_KEY_HEX", ""))

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY)),
    reason="isolated PostgreSQL environment is required",
)

LOJA_A = UUID("ee000000-0000-4000-8000-000000000721")
LOJA_B = UUID("ee000000-0000-4000-8000-000000000722")
DONO = UUID("ee000000-0000-4000-8000-000000000723")
EDITOR = UUID("ee000000-0000-4000-8000-000000000724")
PRODUTO_A1 = UUID("ee000000-0000-4000-8000-000000000725")
PRODUTO_A2 = UUID("ee000000-0000-4000-8000-000000000726")
PRODUTO_B = UUID("ee000000-0000-4000-8000-000000000727")
INTEGRACAO_A = UUID("ee000000-0000-4000-8000-000000000728")
INTEGRACAO_B = UUID("ee000000-0000-4000-8000-000000000729")
ENDPOINT_A = UUID("ee000000-0000-4000-8000-00000000072a")
ENDPOINT_B = UUID("ee000000-0000-4000-8000-00000000072b")
ISSUER = "https://identity.test.autonomia.site"
LEITURA = "commerce.sales.report.read"
EXPORT = "commerce.sales.report.export"

Conexao = psycopg.Connection[tuple[object, ...]]


def _sha(valor: str) -> str:
    return hashlib.sha256(valor.encode()).hexdigest()


def _evento(
    connection: Conexao,
    loja: UUID,
    evento: str,
    tipo: str,
    produto_ref: str,
    compra: str,
    quando: str,
) -> None:
    integracao, endpoint = (
        (INTEGRACAO_A, ENDPOINT_A) if loja == LOJA_A else (INTEGRACAO_B, ENDPOINT_B)
    )
    connection.execute(
        "insert into webhook_event(seller_organization_id,checkout_integration_id,"
        "webhook_endpoint_id,external_event_id_sha256,external_event_id_octets,payload_sha256,"
        "payload_octets,event_type,external_product_id_sha256,external_product_id_octets,"
        "external_purchase_id_sha256,external_purchase_id_octets,occurred_at,signed_at) "
        "values(%s,%s,%s,%s,8,%s,10,%s,%s,8,%s,8,%s,%s) on conflict do nothing",
        (
            loja,
            integracao,
            endpoint,
            _sha(evento),
            _sha(f"payload:{evento}"),
            tipo,
            _sha(produto_ref),
            _sha(compra),
            quando,
            quando,
        ),
    )


@pytest.fixture(scope="module", autouse=True)
def base() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        connection.execute(
            "delete from webhook_event where seller_organization_id in (%s,%s)", (LOJA_A, LOJA_B)
        )
        for pessoa, sub in ((DONO, "sales-owner"), (EDITOR, "sales-editor")):
            connection.execute(
                "insert into identity_user(id,identity_issuer,identity_sub,email_normalized,"
                "email_verified) values(%s,%s,%s,%s,true) on conflict do nothing",
                (pessoa, ISSUER, sub, f"{sub}@example.test"),
            )
        lojas = (
            (
                LOJA_A,
                "loja-vendas-a",
                INTEGRACAO_A,
                ENDPOINT_A,
                ((PRODUTO_A1, "a1"), (PRODUTO_A2, "a2")),
            ),
            (LOJA_B, "loja-vendas-b", INTEGRACAO_B, ENDPOINT_B, ((PRODUTO_B, "b1"),)),
        )
        for loja, slug, integracao, endpoint, produtos in lojas:
            connection.execute(
                "insert into seller_organization(id,slug,display_name,status) "
                "values(%s,%s,%s,'active') on conflict do nothing",
                (loja, slug, slug),
            )
            connection.execute(
                "insert into seller_membership(seller_organization_id,identity_user_id,"
                "role,status) values(%s,%s,'seller_owner','active') on conflict do nothing",
                (loja, DONO),
            )
            connection.execute(
                "insert into checkout_integration(seller_organization_id,id,adapter,"
                "display_name,status) values(%s,%s,'generic_hmac_v1','Checkout','active') "
                "on conflict do nothing",
                (loja, integracao),
            )
            connection.execute(
                "insert into webhook_endpoint(seller_organization_id,id,checkout_integration_id,"
                "endpoint_key_sha256,secret_reference,status) values(%s,%s,%s,%s,%s,'active') "
                "on conflict do nothing",
                (loja, endpoint, integracao, _sha(f"endpoint:{slug}"), f"sales-report-{slug}"),
            )
            for produto, ref in produtos:
                connection.execute(
                    "insert into catalog_product(seller_organization_id,id,slug,title,description,"
                    "status) values(%s,%s,%s,%s,'Produto','draft') on conflict do nothing",
                    (loja, produto, f"curso-{ref}", f"Curso {ref.upper()}"),
                )
                connection.execute(
                    "insert into external_product_mapping(seller_organization_id,"
                    "checkout_integration_id,external_product_id_sha256,"
                    "external_product_id_octets,catalog_product_id) values(%s,%s,%s,8,%s) "
                    "on conflict do nothing",
                    (loja, integracao, _sha(f"produto-{ref}"), produto),
                )
        connection.execute(
            "insert into seller_membership(seller_organization_id,identity_user_id,role,status) "
            "values(%s,%s,'seller_editor','active') on conflict do nothing",
            (LOJA_A, EDITOR),
        )
        # Loja A: 3 compras do A1 (uma com replay do evento e outra estornada no
        # dia seguinte), 1 do A2. 23:30 em São Paulo de 01/10 ainda é 01/10.
        _evento(
            connection,
            LOJA_A,
            "e1",
            "purchase.approved",
            "produto-a1",
            "c1",
            "2026-10-01T12:00:00Z",
        )
        _evento(
            connection,
            LOJA_A,
            "e1",
            "purchase.approved",
            "produto-a1",
            "c1",
            "2026-10-01T12:00:00Z",
        )
        _evento(
            connection,
            LOJA_A,
            "e2",
            "purchase.approved",
            "produto-a1",
            "c2",
            "2026-10-02T02:30:00Z",
        )
        _evento(
            connection,
            LOJA_A,
            "e3",
            "purchase.approved",
            "produto-a1",
            "c3",
            "2026-10-02T15:00:00Z",
        )
        _evento(
            connection,
            LOJA_A,
            "e4",
            "purchase.refunded",
            "produto-a1",
            "c3",
            "2026-10-03T15:00:00Z",
        )
        _evento(
            connection,
            LOJA_A,
            "e5",
            "purchase.approved",
            "produto-a2",
            "c4",
            "2026-10-02T15:00:00Z",
        )
        # Reaprovação da mesma compra não conta segunda venda.
        _evento(
            connection,
            LOJA_A,
            "e6",
            "purchase.approved",
            "produto-a2",
            "c4",
            "2026-10-04T15:00:00Z",
        )
        _evento(
            connection,
            LOJA_B,
            "f1",
            "purchase.approved",
            "produto-b1",
            "d1",
            "2026-10-02T15:00:00Z",
        )
        connection.commit()


def _context(connection: Conexao, actor: UUID, capability: str, seller: UUID) -> None:
    issued = int(time.time())
    expires = issued + 60
    payload = f"{actor}:{seller}:{capability}:{issued}:{expires}"
    signature = hmac.new(CONTEXT_KEY, payload.encode(), hashlib.sha256).hexdigest()
    connection.execute(
        "select app_private.apply_request_context(%s,%s,%s,%s,%s,%s)",
        (actor, seller, capability, issued, expires, signature),
    )


def _relatorio(
    loja: UUID = LOJA_A,
    de: date = date(2026, 10, 1),
    ate: date = date(2026, 10, 31),
    produto: UUID | None = None,
    pessoa: UUID = DONO,
    capacidade: str = LEITURA,
) -> dict[str, Any]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, pessoa, capacidade, loja)
        row = connection.execute(
            "select app_private.read_sales_report(%s,%s,%s,%s,%s)", (pessoa, loja, de, ate, produto)
        ).fetchone()
    assert row is not None
    return cast(dict[str, Any], row[0])


def _export(
    loja: UUID = LOJA_A, de: date = date(2026, 10, 1), ate: date = date(2026, 10, 31)
) -> dict[str, Any]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, DONO, EXPORT, loja)
        row = connection.execute(
            "select app_private.export_sales_rows(%s,%s,%s,%s,null,%s)",
            (DONO, loja, de, ate, uuid4()),
        ).fetchone()
        connection.commit()
    assert row is not None
    return cast(dict[str, Any], row[0])


def _linhas(relatorio: dict[str, Any]) -> set[tuple[str, str, int, int]]:
    return {
        (linha["day"], linha["product_title"], linha["approved"], linha["refunded"])
        for linha in relatorio["rows"]
    }


def test_conta_cada_compra_uma_vez_por_dia_e_produto() -> None:
    # c2 é 02:30Z, 23:30 em São Paulo: entra em 01/10. O replay de e1 e a
    # reaprovação de c4 não contam de novo.
    assert _linhas(_relatorio()) == {
        ("2026-10-01", "Curso A1", 2, 0),
        ("2026-10-02", "Curso A1", 1, 0),
        ("2026-10-02", "Curso A2", 1, 0),
        ("2026-10-03", "Curso A1", 0, 1),
    }


def test_filtra_por_produto_e_por_periodo() -> None:
    assert _linhas(_relatorio(produto=PRODUTO_A2)) == {("2026-10-02", "Curso A2", 1, 0)}
    assert _linhas(_relatorio(de=date(2026, 10, 3), ate=date(2026, 10, 3))) == {
        ("2026-10-03", "Curso A1", 0, 1)
    }


def test_loja_a_nao_ve_a_b_e_o_contexto_de_outra_loja_e_recusado() -> None:
    assert _linhas(_relatorio(loja=LOJA_B)) == {("2026-10-02", "Curso B1", 1, 0)}
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        _context(connection, DONO, LEITURA, LOJA_B)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "select app_private.read_sales_report(%s,%s,%s,%s,null)",
                (DONO, LOJA_A, date(2026, 10, 1), date(2026, 10, 31)),
            )


def test_so_dono_e_admin_leem_e_a_capacidade_e_conferida() -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _relatorio(pessoa=EDITOR)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _relatorio(capacidade=EXPORT)


def test_periodo_invertido_ou_longo_demais_e_recusado() -> None:
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _relatorio(de=date(2026, 10, 2), ate=date(2026, 10, 1))
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _relatorio(de=date(2025, 1, 1), ate=date(2026, 10, 31))


def test_saida_sem_dado_pessoal_nem_payload() -> None:
    texto = str(_relatorio()) + str(_export())
    for proibido in ("@", "identity", "email", "payload", _sha("c1")):
        assert proibido not in texto


def test_export_traz_uma_linha_por_compra_e_audita() -> None:
    export = _export()
    linhas = {(linha["product_title"], linha["status"]) for linha in export["rows"]}
    assert len(export["rows"]) == 4
    assert linhas == {("Curso A1", "approved"), ("Curso A1", "refunded"), ("Curso A2", "approved")}
    assert all(len(linha["purchase_ref"]) == 12 for linha in export["rows"])
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select count(*),max((metadata->>'rows')::int) from audit_event "
            "where seller_organization_id=%s and action='commerce.sales_exported' "
            "and actor_identity_user_id=%s",
            (LOJA_A, DONO),
        ).fetchone()
    assert row is not None and row[0] >= 1 and row[1] == 4


def test_export_tem_limite_de_taxa() -> None:
    erro = None
    for _ in range(8):
        try:
            _export()
        except psycopg.errors.DatabaseError as falha:
            erro = falha
            break
    assert erro is not None and erro.sqlstate == "P4291"
