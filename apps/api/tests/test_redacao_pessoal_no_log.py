"""Dado pessoal não sai no log da API, nem dentro do traceback (#549).

O redator de `logging_setup` pegava o que tem cara de segredo. E-mail, CPF,
CNPJ, query string e valor de cabeçalho não têm, e saíam inteiros. Os testes
leem a linha que o processo de fato escreve, não o retorno de uma função.
"""

import json
from collections.abc import Iterator
from io import StringIO

import pytest

from appsell_api import logging_setup
from appsell_api.redacao_pessoal import redigir_dado_pessoal

EMAIL = "ana.silva+loja@exemplo.com.br"
CPF = "529.982.247-25"
CPF_SEM_PONTO = "52998224725"
CNPJ = "11.222.333/0001-81"
VALOR_DO_LINK = "abc123"
BEARER = "Bearer eyJhbGciOi.curto"


@pytest.fixture(autouse=True)
def _devolve_a_configuracao_da_aplicacao() -> Iterator[None]:
    try:
        yield
    finally:
        logging_setup.configure_logging_from_environment()


def _linha_de_producao(registrar) -> str:  # type: ignore[no-untyped-def]
    fluxo = StringIO()
    logging_setup.configure_logging(environment="production", log_level="INFO", stream=fluxo)
    registrar(logging_setup.get_logger("teste"))
    linhas = [linha for linha in fluxo.getvalue().splitlines() if linha.strip()]
    assert len(linhas) == 1
    return linhas[0]


def test_excecao_com_dado_pessoal_sai_redigida_no_json() -> None:
    def registrar(logger) -> None:  # type: ignore[no-untyped-def]
        try:
            raise RuntimeError(
                f"envio para {EMAIL} falhou; cpf {CPF} cnpj {CNPJ}; "
                f"GET https://sell.test/c/link?token={VALOR_DO_LINK}&email={EMAIL}; "
                f"headers={{'authorization': '{BEARER}', 'cookie': 'sid={VALOR_DO_LINK}'}}"
            )
        except RuntimeError:
            logger.exception("envio_falhou", destino=EMAIL, documento=CPF_SEM_PONTO)

    linha = _linha_de_producao(registrar)

    for vazado in (EMAIL, CPF, CPF_SEM_PONTO, CNPJ, VALOR_DO_LINK, "eyJhbGciOi"):
        assert vazado not in linha, vazado
    registro = json.loads(linha)
    assert registro["level"] == "error"
    # O que diz onde e o que falhou continua no log.
    assert "https://sell.test/c/link?<REDACTED>" in registro["exception"]
    assert "RuntimeError: envio para <REDACTED> falhou" in registro["exception"]
    assert registro["destino"] == "<REDACTED>"


def test_a_query_sai_e_o_host_da_url_fica() -> None:
    """Antes a query com e-mail sobrevivia para o host não sumir junto (#549).

    Agora a query sai, e o host e a porta continuam dizendo a qual serviço o
    erro se refere.
    """
    valor = "http://api.internal:8080/v1/x?next=alice@autonomia.site"
    linha = _linha_de_producao(lambda logger: logger.info("evento", campo=valor))
    assert json.loads(linha)["campo"] == "http://api.internal:8080/v1/x?<REDACTED>"


@pytest.mark.parametrize(
    "valor",
    [
        "2026-11-05T10:00:16.123Z",
        "pedido 12345678901",
        "id 20261105100016",
        "versao 1.2.3-rc.4",
        "x-authorization-hint: ok",
        "https://sell.test/s/hub2you/p/curso",
        "usuario@sem-dominio",
    ],
)
def test_o_que_nao_e_dado_pessoal_sobrevive(valor: str) -> None:
    linha = _linha_de_producao(lambda logger: logger.info("evento", campo=valor))
    assert json.loads(linha)["campo"] == valor


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        (f"para {EMAIL}.", "para <REDACTED>"),
        (f"cpf {CPF_SEM_PONTO}", "cpf <REDACTED>"),
        ("cpf 111.111.111-11", "cpf 111.111.111-11"),
        (f"cnpj {CNPJ}", "cnpj <REDACTED>"),
        ("Authorization: Bearer x\nseguinte", "Authorization: <REDACTED>\nseguinte"),
        ('{"cookie": "a=b; c=d", "accept": "*/*"}', '{"cookie": "<REDACTED>", "accept": "*/*"}'),
        ("https://h.test/a?b=c fim", "https://h.test/a?<REDACTED> fim"),
    ],
)
def test_regras_do_redator(entrada: str, esperado: str) -> None:
    assert redigir_dado_pessoal(entrada) == esperado
