"""Código da compra e cópia marcada no Postgres real (#559, parte B1).

A loja e o PDF de apoio com download liberado são montados com os helpers do
teste da política de PDF, o mesmo caminho que o download de hoje autoriza.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from test_postgres_pdf_access_policy import (
    ADMIN,
    BUYER,
    EDITOR,
    OTHER_OWNER,
    OWNER,
    Asset,
    Graph,
    add_asset,
    apply_context,
    create_graph,
    digest,
    publish,
    seed_entitlement,
    seed_identities,
    set_policy,
)

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")
RUNTIME_URL = os.environ.get("APPSELL_TEST_POSTGRES_RUNTIME_URL")
CONTEXT_KEY_HEX = os.environ.get("APPSELL_TEST_CONTEXT_KEY_HEX")

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not RUNTIME_URL or not CONTEXT_KEY_HEX,
    reason="Postgres de teste não configurado",
)

ALFABETO = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
DOWNLOAD = "library.asset.download"
RESOLVER = "seller.purchase_code.resolve"
# O bucket do blob que `add_asset` cria: a cópia mora no mesmo bucket do original.
BUCKET = "pytest-policy"


@pytest.fixture(scope="module", autouse=True)
def identities() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        seed_identities(connection)
        connection.commit()


def _loja_com_pdf(owner: UUID = OWNER) -> tuple[Graph, Asset, Asset, UUID]:
    """Loja ativa, PDF de apoio com download liberado, vídeo, compra ativa do BUYER."""
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        graph = create_graph(connection, owner=owner, staff=owner == OWNER)
        pdf = add_asset(connection, graph, media_type="application/pdf", position=0)
        video = add_asset(connection, graph, media_type="video/mp4", position=1)
        connection.commit()
    set_policy(
        owner,
        graph,
        pdf,
        content_role="support_material",
        download_allowed=True,
        expected_version=1,
    )
    publication = publish(graph, expected_course_version=2)
    entitlement = seed_entitlement(graph, publication)
    return graph, pdf, video, entitlement


def _prepara(buyer: UUID, entitlement: UUID, asset: UUID, capability: str = DOWNLOAD) -> Any:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, buyer, None, capability)
        row = connection.execute(
            "select * from app_private.prepare_watermarked_download(%s,%s,%s)",
            (buyer, entitlement, asset),
        ).fetchone()
        connection.commit()
        return row


def _registra(
    entitlement: UUID,
    asset: UUID,
    version: int,
    object_key: str,
    sha: str,
    bucket: str = BUCKET,
) -> Any:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, BUYER, None, DOWNLOAD)
        row = connection.execute(
            "select * from app_private.record_watermarked_asset_copy(%s,%s,%s,%s,%s,%s,%s,%s)",
            (BUYER, entitlement, asset, version, bucket, object_key, sha, 2048),
        ).fetchone()
        connection.commit()
        return row


def _resolve(actor: UUID, seller: UUID, code: str, context_seller: UUID | None = None) -> list[Any]:
    assert RUNTIME_URL
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, actor, context_seller or seller, RESOLVER)
        return connection.execute(
            "select * from app_private.resolve_purchase_code(%s,%s,%s)", (actor, seller, code)
        ).fetchall()


def test_o_comprador_recebe_um_codigo_estavel_e_a_chave_da_copia_na_loja() -> None:
    # Pega: código novo a cada download (a loja não acharia a compra) e chave
    # da cópia fora do prefixo privado da loja.
    graph, pdf, _video, entitlement = _loja_com_pdf()
    seller, code, version, bucket, prefixo, *cache = _prepara(BUYER, entitlement, pdf.asset)
    assert seller == graph.seller
    assert len(code) == 10 and all(letra in ALFABETO for letra in code)
    assert bucket == BUCKET
    assert prefixo == f"tenants/{graph.seller}/watermarked/{entitlement}/{pdf.asset}/v{version}-"
    assert cache == [None, None, None]
    assert _prepara(BUYER, entitlement, pdf.asset)[1] == code


def test_compras_diferentes_recebem_codigos_diferentes() -> None:
    _graph, pdf, _video, primeira = _loja_com_pdf()
    _graph2, pdf2, _video2, segunda = _loja_com_pdf()
    assert _prepara(BUYER, primeira, pdf.asset)[1] != _prepara(BUYER, segunda, pdf2.asset)[1]


def test_a_copia_registrada_volta_do_cache_e_a_primeira_gravacao_vale() -> None:
    # Pega: dois downloads ao mesmo tempo deixando o cache com o hash de uma
    # geração e a chave de outra. Cada geração tem a própria chave.
    _graph, pdf, _video, entitlement = _loja_com_pdf()
    _seller, _code, version, _bucket, prefixo, *_ = _prepara(BUYER, entitlement, pdf.asset)
    sha = digest(f"copia:{entitlement}")
    chave = f"{prefixo}{sha}.pdf"
    assert _registra(entitlement, pdf.asset, version, chave, sha) == (BUCKET, chave, sha, 2048)
    outra = digest("outra")
    assert _registra(entitlement, pdf.asset, version, f"{prefixo}{outra}.pdf", outra) == (
        BUCKET,
        chave,
        sha,
        2048,
    )
    assert _prepara(BUYER, entitlement, pdf.asset)[5:] == (chave, sha, 2048)


def test_a_copia_nao_sai_do_prefixo_da_loja() -> None:
    # Pega: uma chave forjada apontando para o original, para outra loja ou
    # para bytes que não são os do hash gravado.
    graph, pdf, _video, entitlement = _loja_com_pdf()
    _seller, _code, version, _bucket, prefixo, *_ = _prepara(BUYER, entitlement, pdf.asset)
    sha = digest("copia")
    for forjada in (
        f"tenants/{graph.seller}/assets/{pdf.asset}/v{version}/original.pdf",
        f"tenants/{uuid4()}/watermarked/{entitlement}/{pdf.asset}/v{version}-{sha}.pdf",
        f"{prefixo}{digest('outros bytes')}.pdf",
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            _registra(entitlement, pdf.asset, version, forjada, sha)


def test_a_copia_mora_no_bucket_do_original() -> None:
    # Pega: um bucket de fora servindo outro arquivo como se fosse a cópia.
    _graph, pdf, _video, entitlement = _loja_com_pdf()
    _seller, _code, version, _bucket, prefixo, *_ = _prepara(BUYER, entitlement, pdf.asset)
    sha = digest("copia")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _registra(entitlement, pdf.asset, version, f"{prefixo}{sha}.pdf", sha, "bucket-de-fora")


def test_registrar_sem_codigo_ou_com_outra_versao_e_recusado() -> None:
    _graph, pdf, _video, entitlement = _loja_com_pdf()
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        _registra(entitlement, pdf.asset, 1, "x", digest("x"))
    _seller, _code, version, _bucket, prefixo, *_ = _prepara(BUYER, entitlement, pdf.asset)
    sha = digest("y")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _registra(entitlement, pdf.asset, version + 1, f"{prefixo}{sha}.pdf", sha)


@pytest.mark.parametrize(
    ("comprador", "capacidade"),
    [(OWNER, DOWNLOAD), (BUYER, "library.asset.read"), (BUYER, "library.read")],
)
def test_so_o_dono_da_compra_no_contexto_de_download_prepara(
    comprador: UUID, capacidade: str
) -> None:
    # Pega: outra pessoa (ou o contexto de leitura) emitindo o código da compra.
    _graph, pdf, _video, entitlement = _loja_com_pdf()
    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        _prepara(comprador, entitlement, pdf.asset, capacidade)


def test_so_pdf_com_download_liberado_ganha_copia() -> None:
    _graph, _pdf, video, entitlement = _loja_com_pdf()
    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        _prepara(BUYER, entitlement, video.asset)


def test_a_loja_dona_resolve_o_codigo_como_foi_lido() -> None:
    graph, pdf, _video, entitlement = _loja_com_pdf()
    code = _prepara(BUYER, entitlement, pdf.asset)[1]
    lido = f" {code[:5].lower()}-{code[5:]} "
    for actor in (OWNER, ADMIN):
        linhas = _resolve(actor, graph.seller, lido)
        assert [(linha[0], linha[1]) for linha in linhas] == [(entitlement, graph.product)]


def test_a_loja_a_nao_resolve_o_codigo_da_loja_b() -> None:
    # Pega: o código de uma compra circulando e outra loja descobrindo de quem é.
    loja_a, pdf, _video, entitlement = _loja_com_pdf()
    code = _prepara(BUYER, entitlement, pdf.asset)[1]
    loja_b, *_ = _loja_com_pdf(owner=OTHER_OWNER)
    # Na própria loja, o código da loja A não existe.
    assert _resolve(OTHER_OWNER, loja_b.seller, code) == []
    # Pedindo pela loja A, sem ser membro dela, é recusado.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _resolve(OTHER_OWNER, loja_a.seller, code)
    # Contexto assinado para a loja B não vale para resolver na loja A.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _resolve(OTHER_OWNER, loja_a.seller, code, context_seller=loja_b.seller)


def test_editor_e_comprador_nao_resolvem() -> None:
    graph, pdf, _video, entitlement = _loja_com_pdf()
    code = _prepara(BUYER, entitlement, pdf.asset)[1]
    for actor in (EDITOR, BUYER):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _resolve(actor, graph.seller, code)


def test_codigo_fora_do_alfabeto_volta_vazio() -> None:
    graph, *_ = _loja_com_pdf()
    for lixo in ("", "0000000000", "ABCDEFGHJ", "ABCDEFGHJKM", "ABC'; --xx"):
        assert _resolve(OWNER, graph.seller, lixo) == []


def test_o_runtime_nao_le_as_tabelas_direto() -> None:
    assert RUNTIME_URL
    for tabela in ("purchase_code", "watermarked_asset_copy"):
        with psycopg.connect(RUNTIME_URL) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(f"select count(*) from app_private.{tabela}")  # noqa: S608 - tabela vem da tupla fixa acima


def test_as_tabelas_tem_rls_forcada_e_funcoes_sao_do_dono() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        tabelas = connection.execute(
            "select relname,relrowsecurity,relforcerowsecurity,pg_get_userbyid(relowner) "
            "from pg_class where oid in ('app_private.purchase_code'::regclass,"
            "'app_private.watermarked_asset_copy'::regclass) order by relname"
        ).fetchall()
        assert tabelas == [
            ("purchase_code", True, True, "appsell_owner"),
            ("watermarked_asset_copy", True, True, "appsell_owner"),
        ]
        funcoes = connection.execute(
            "select proname,prosecdef,pg_get_userbyid(proowner),proconfig from pg_proc "
            "where pronamespace='app_private'::regnamespace and proname in "
            "('prepare_watermarked_download','record_watermarked_asset_copy',"
            "'resolve_purchase_code') order by proname"
        ).fetchall()
        assert len(funcoes) == 3
        for _nome, definer, dono, config in funcoes:
            assert definer is True and dono == "appsell_owner"
            assert config == ["search_path=pg_catalog, app_private, public, pg_temp"]


def test_o_schema_ready_conhece_a_versao() -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        assert connection.execute("select app_private.schema_ready()").fetchone() == (True,)
