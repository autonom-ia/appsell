"""Isolamento do estado que vive em `main.app`.

`main.app` é criado uma vez por processo e compartilhado por todos os testes.
O que um escreve em `app.state` sobra para o seguinte, e a falha aparece longe
da causa: um teste que preenche o diagnóstico do antivírus faz o `/health/ready`
de outro responder com um campo a mais, e uma comparação de corpo inteiro
quebra num arquivo que não tem nada a ver com antivírus.

Estes atributos são os que os testes escrevem direto, sem passar pelo
`lifespan`, que é quem os inicializa em produção.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from appsell_api import main

_ESTADO_POR_TESTE = ("asset_scanner_diagnostico", "ever_ready")


@pytest.fixture(autouse=True)
def _estado_do_app_limpo() -> Iterator[None]:
    _limpa()
    yield
    _limpa()


def _limpa() -> None:
    for atributo in _ESTADO_POR_TESTE:
        if hasattr(main.app.state, atributo):
            delattr(main.app.state, atributo)
