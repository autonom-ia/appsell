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

ROTAS = Path(__file__).parents[1] / "src" / "appsell_api" / "catalog_routes.py"
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


def test_toda_rota_que_chama_as_funcoes_passa_pelo_portao() -> None:
    """Descobre as rotas pelo código: nenhuma lista mantida à mão."""
    arvore = ast.parse(ROTAS.read_text())
    chamadoras: list[str] = []
    for no in ast.walk(arvore):
        if not isinstance(no, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        textos = [
            constante.value
            for constante in ast.walk(no)
            if isinstance(constante, ast.Constant) and isinstance(constante.value, str)
        ]
        if not any(funcao in texto for funcao in FUNCOES_DO_PORTAO for texto in textos):
            continue
        chamadoras.append(no.name)
        chamadas = {
            chamada.func.id
            for chamada in ast.walk(no)
            if isinstance(chamada, ast.Call) and isinstance(chamada.func, ast.Name)
        }
        assert PORTAO in chamadas, f"{no.name} chama a função sem o portão da lista"
    # As duas rotas da fase 2 existem; se sumirem, o teste perde o sentido.
    assert sorted(chamadoras) == ["create_product_offer", "set_default_offer"]


def test_uuid_aceita_maiusculas_como_o_resto_da_api() -> None:
    loja = uuid4()
    env = {MULTI_OFFER_SELLERS_ENV: str(loja).upper()}
    assert multi_offer_enabled(UUID(str(loja)), env) is True
