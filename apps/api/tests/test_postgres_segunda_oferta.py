"""A segunda oferta no banco de verdade, fase 2 da #553 (0068).

Cada teste monta uma loja e um produto novos pelos mesmos caminhos do editor
(`create_graph`, `save_draft`, `publish_with_access`) e chama as funções como o
runtime chama: conexão `appsell_runtime` com contexto assinado.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from test_postgres_access_links import (
    ADMIN_URL,
    CONTEXT_KEY_HEX,
    RUNTIME_URL,
    SELLER_USER,
    apply_context,
    bind_payout_account,
    create_graph,
    digest,
    publish_with_access,
    save_draft,
    seed_identities,
)

__all__ = ["seed_identities"]  # fixture autouse importada: semeia as identidades

pytestmark = pytest.mark.skipif(
    not all((ADMIN_URL, RUNTIME_URL, CONTEXT_KEY_HEX)),
    reason="isolated PostgreSQL environment is required",
)


def _cria_oferta(
    seller_id: UUID, product_id: UUID, *, price: int = 9700, contexto: UUID | None = None
) -> UUID:
    assert RUNTIME_URL
    offer_id = uuid4()
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, SELLER_USER, contexto or seller_id, "catalog.offer.create")
        connection.execute(
            "select offer_id from app_private.create_product_offer("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                SELLER_USER,
                seller_id,
                product_id,
                offer_id,
                "Oferta extra",
                "",
                price,
                "BRL",
                "one_time",
                uuid4(),
                digest(f"oferta:{offer_id}"),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
    return offer_id


def _oferta(seller_id: UUID, offer_id: UUID) -> tuple[Any, ...]:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        linha = connection.execute(
            "select status, version, is_default, unit_amount_minor from offer "
            "where seller_organization_id=%s and id=%s",
            (seller_id, offer_id),
        ).fetchone()
    assert linha is not None
    return linha


def _torna_padrao(seller_id: UUID, product_id: UUID, offer_id: UUID) -> tuple[Any, ...]:
    assert RUNTIME_URL
    versao = _oferta(seller_id, offer_id)[1]
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, SELLER_USER, seller_id, "catalog.offer.default.update")
        linha = connection.execute(
            "select offer_id, previous_default_offer_id, changed "
            "from app_private.set_default_offer(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                SELLER_USER,
                seller_id,
                product_id,
                offer_id,
                versao,
                uuid4(),
                digest(f"padrao:{offer_id}:{uuid4()}"),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()
    assert linha is not None
    return linha


def _transiciona(seller_id: UUID, product_id: UUID, offer_id: UUID, de: str, para: str) -> None:
    assert RUNTIME_URL
    versao = _oferta(seller_id, offer_id)[1]
    with psycopg.connect(RUNTIME_URL) as connection:
        apply_context(connection, SELLER_USER, seller_id, "catalog.offer.status.update")
        connection.execute(
            "select offer_status from app_private.transition_offer_status("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                SELLER_USER,
                seller_id,
                product_id,
                offer_id,
                de,
                para,
                versao,
                uuid4(),
                digest(f"status:{offer_id}:{para}:{uuid4()}"),
                uuid4(),
            ),
        ).fetchone()
        connection.commit()


def _produto_vendido() -> tuple[UUID, UUID, UUID]:
    """Produto publicado e à venda: a padrão está `active`."""
    seller_id, product_id, course_id = create_graph()
    bind_payout_account(seller_id)
    offer_id = save_draft(seller_id, product_id, course_id)
    publish_with_access(seller_id, product_id, course_id, offer_id, access_mode="paid")
    assert _oferta(seller_id, offer_id)[0] == "active"
    return seller_id, product_id, offer_id


def _padroes(seller_id: UUID, product_id: UUID) -> list[UUID]:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        linhas = connection.execute(
            "select id from offer where seller_organization_id=%s "
            "and catalog_product_id=%s and is_default",
            (seller_id, product_id),
        ).fetchall()
    return [linha[0] for linha in linhas]


def _sqlstate(acao: Any) -> str | None:
    with pytest.raises(psycopg.errors.Error) as recusa:
        acao()
    return recusa.value.sqlstate


def test_a_segunda_oferta_nasce_rascunho_e_nao_padrao() -> None:
    seller_id, product_id, offer_id = _produto_vendido()
    extra = _cria_oferta(seller_id, product_id)
    assert _oferta(seller_id, extra)[:3] == ("draft", 1, False)
    assert _padroes(seller_id, product_id) == [offer_id]


def test_outra_loja_nao_cria_oferta_no_produto() -> None:
    seller_id, product_id, _ = _produto_vendido()
    outra, _, _ = create_graph()
    assert _sqlstate(lambda: _cria_oferta(seller_id, product_id, contexto=outra)) == "28000"


def test_rascunho_nao_vira_padrao_de_produto_a_venda() -> None:
    seller_id, product_id, offer_id = _produto_vendido()
    extra = _cria_oferta(seller_id, product_id)
    assert _sqlstate(lambda: _torna_padrao(seller_id, product_id, extra)) == "P4093"
    assert _padroes(seller_id, product_id) == [offer_id]


def test_troca_para_oferta_a_venda_de_mesmo_preco() -> None:
    seller_id, product_id, offer_id = _produto_vendido()
    extra = _cria_oferta(seller_id, product_id, price=9700)
    _transiciona(seller_id, product_id, extra, "draft", "active")
    linha = _torna_padrao(seller_id, product_id, extra)
    assert (linha[1], bool(linha[2])) == (offer_id, True)
    assert _padroes(seller_id, product_id) == [extra]


def test_troca_que_muda_o_preco_de_produto_a_venda_e_recusada() -> None:
    seller_id, product_id, offer_id = _produto_vendido()
    extra = _cria_oferta(seller_id, product_id, price=12300)
    _transiciona(seller_id, product_id, extra, "draft", "active")
    assert _sqlstate(lambda: _torna_padrao(seller_id, product_id, extra)) == "P4093"
    assert _padroes(seller_id, product_id) == [offer_id]


def test_a_padrao_nao_sai_de_venda_com_outra_oferta() -> None:
    seller_id, product_id, offer_id = _produto_vendido()
    _cria_oferta(seller_id, product_id)
    for destino in ("archived", "draft"):

        def sai(destino: str = destino) -> None:
            _transiciona(seller_id, product_id, offer_id, "active", destino)

        assert _sqlstate(sai) == "P4093"
    assert _oferta(seller_id, offer_id)[0] == "active"


def test_limite_de_ofertas_por_produto() -> None:
    seller_id, product_id, _ = _produto_vendido()
    for _ in range(9):
        _cria_oferta(seller_id, product_id)
    assert _sqlstate(lambda: _cria_oferta(seller_id, product_id)) == "P4094"


def test_trocas_concorrentes_terminam_com_uma_padrao_so() -> None:
    seller_id, product_id, offer_id = _produto_vendido()
    ofertas = [_cria_oferta(seller_id, product_id) for _ in range(4)]
    for extra in ofertas:
        _transiciona(seller_id, product_id, extra, "draft", "active")

    def tenta(alvo: UUID) -> str:
        try:
            _torna_padrao(seller_id, product_id, alvo)
        except psycopg.errors.Error as erro:
            return erro.sqlstate or "?"
        return "ok"

    with ThreadPoolExecutor(max_workers=4) as pool:
        resultados = list(pool.map(tenta, ofertas))
    # Ninguém morre em deadlock; perder a corrida é conflito de versão ou sucesso.
    assert "40P01" not in resultados
    assert set(resultados) <= {"ok", "40001"}
    assert len(_padroes(seller_id, product_id)) == 1
    assert offer_id not in ofertas
