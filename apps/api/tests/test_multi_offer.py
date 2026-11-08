"""A lista de lojas com várias ofertas e o portão das rotas (#553, fase 2)."""

from __future__ import annotations

import ast
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from appsell_api.multi_offer import (
    MULTI_OFFER_SELLERS_ENV,
    MultiOfferConfigError,
    MultiOfferDisabledError,
    multi_offer_enabled,
    multi_offer_sellers,
    require_multi_offer,
)

PACOTE = Path(__file__).parents[1] / "src" / "appsell_api"
FUNCOES_DO_PORTAO = ("app_private.create_product_offer(", "app_private.set_default_offer(")
PORTAO = "_require_multi_offer_or_reject"


def test_vazia_desliga_para_todas_as_lojas() -> None:
    assert multi_offer_sellers({}) == frozenset()
    assert multi_offer_sellers({MULTI_OFFER_SELLERS_ENV: " , "}) == frozenset()
    assert multi_offer_enabled(uuid4(), {}) is False


def test_so_a_loja_da_lista_passa() -> None:
    dentro, fora = uuid4(), uuid4()
    env = {MULTI_OFFER_SELLERS_ENV: f" {dentro} ,"}
    assert multi_offer_enabled(dentro, env) is True
    assert multi_offer_enabled(fora, env) is False
    require_multi_offer(dentro, env)
    with pytest.raises(MultiOfferDisabledError):
        require_multi_offer(fora, env)


def test_malformada_recusa_em_vez_de_adivinhar() -> None:
    with pytest.raises(MultiOfferConfigError):
        multi_offer_sellers({MULTI_OFFER_SELLERS_ENV: f"{uuid4()},hub2you"})


def _chamadoras_sem_portao(fonte: str) -> tuple[list[str], list[str]]:
    """Funções que citam as funções do banco, e quais delas não passam antes pelo portão.

    "Antes" é pela linha: a chamada do portão precisa aparecer antes do texto
    SQL que chama a função. Uma rota que chama o banco e só depois o portão
    gravaria a oferta e responderia 403.
    """
    arvore = ast.parse(fonte)
    chamadoras: list[str] = []
    sem_portao: list[str] = []
    for no in ast.walk(arvore):
        if not isinstance(no, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        linhas_sql = [
            constante.lineno
            for constante in ast.walk(no)
            if isinstance(constante, ast.Constant)
            and isinstance(constante.value, str)
            and any(funcao in constante.value for funcao in FUNCOES_DO_PORTAO)
        ]
        if not linhas_sql:
            continue
        chamadoras.append(no.name)
        linhas_portao = [
            chamada.lineno
            for chamada in ast.walk(no)
            if isinstance(chamada, ast.Call)
            and isinstance(chamada.func, ast.Name)
            and chamada.func.id == PORTAO
        ]
        if not linhas_portao or min(linhas_portao) > min(linhas_sql):
            sem_portao.append(no.name)
    return chamadoras, sem_portao


def test_toda_funcao_do_pacote_que_chama_o_banco_passa_antes_pelo_portao() -> None:
    """Descobre as chamadoras no pacote inteiro: nenhuma lista mantida à mão."""
    chamadoras: list[str] = []
    for arquivo in sorted(PACOTE.glob("*.py")):
        encontradas, sem_portao = _chamadoras_sem_portao(arquivo.read_text())
        assert not sem_portao, f"{arquivo.name}: {sem_portao} chamam o banco sem o portão antes"
        chamadoras.extend(f"{arquivo.name}:{nome}" for nome in encontradas)
    # As duas rotas da fase 2 existem; se sumirem, o teste perde o sentido.
    assert sorted(chamadoras) == [
        "catalog_routes.py:create_product_offer",
        "catalog_routes.py:set_default_offer",
    ]


def test_o_detector_acusa_rota_sem_portao_e_portao_depois_do_banco() -> None:
    """Fixture negativa: prova que o detector não passa tudo."""
    sem = """
async def rota(session):
    await session.execute("select * from app_private.create_product_offer(1)")
"""
    depois = """
async def rota(session, internal, seller):
    await session.execute("select * from app_private.set_default_offer(1)")
    await _require_multi_offer_or_reject(internal, seller)
"""
    certa = """
async def rota(session, internal, seller):
    await _require_multi_offer_or_reject(internal, seller)
    await session.execute("select * from app_private.set_default_offer(1)")
"""
    assert _chamadoras_sem_portao(sem) == (["rota"], ["rota"])
    assert _chamadoras_sem_portao(depois) == (["rota"], ["rota"])
    assert _chamadoras_sem_portao(certa) == (["rota"], [])


def test_uuid_aceita_maiusculas_como_o_resto_da_api() -> None:
    loja = uuid4()
    env = {MULTI_OFFER_SELLERS_ENV: str(loja).upper()}
    assert multi_offer_enabled(UUID(str(loja)), env) is True
