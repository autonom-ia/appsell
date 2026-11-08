"""Dado pessoal não sai no log da API, nem dentro do traceback (#549).

O redator de `logging_setup` pegava o que tem cara de segredo. E-mail, CPF,
CNPJ, query string e valor de cabeçalho não têm, e saíam inteiros. Os testes
leem a linha que o processo de fato escreve, não o retorno de uma função.
"""

import json
import logging
import time
from collections.abc import Iterator
from io import StringIO

import pytest

from appsell_api import logging_setup
from appsell_api.redacao_pessoal import TETO_DE_TEXTO, limitar_tamanho, redigir_dado_pessoal

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


# --- Revisão do PR #576 -------------------------------------------------------


def _linhas(ambiente: str, registrar) -> list[str]:  # type: ignore[no-untyped-def]
    fluxo = StringIO()
    logging_setup.configure_logging(environment=ambiente, log_level="INFO", stream=fluxo)
    registrar()
    return [linha for linha in fluxo.getvalue().splitlines() if linha.strip()]


def test_m2_nao_derruba_o_log_e_sai_na_linha_escrita() -> None:
    """`"²".isdigit()` é verdadeiro e `int("²")` levanta: "50 m²" derrubava o log."""

    def registrar() -> None:
        logger = logging_setup.get_logger("teste")
        logger.info("imovel", descricao="Apartamento 50 m²")
        try:
            raise RuntimeError("Apartamento 50 m² ① ³")
        except RuntimeError:
            logger.exception("falhou")
        logging.getLogger("biblioteca").warning("Apartamento 50 m²")

    linhas = _linhas("production", registrar)

    registros = [json.loads(linha) for linha in linhas]
    assert len(registros) == 3
    assert registros[0]["descricao"] == "Apartamento 50 m²"
    assert "RuntimeError: Apartamento 50 m² ① ³" in registros[1]["exception"]
    assert registros[2]["event"] == "Apartamento 50 m²"


def test_m2_no_log_de_console_local() -> None:
    linhas = _linhas(
        "local", lambda: logging_setup.get_logger("teste").info("imovel", descricao="50 m²")
    )
    assert len(linhas) == 1
    assert "50 m²" in linhas[0]


@pytest.mark.parametrize(
    ("nome", "registrar", "vazados"),
    [
        (
            "cabecalho antes de linha de traceback",
            lambda logger: _excecao(logger, 'Authorization: Bearer abc123\n  File "/app/x.py"'),
            ["abc123"],
        ),
        (
            "e-mail com acento",
            lambda logger: logger.info("evento", destino="josé@gmail.com"),
            ["jos", "gmail"],
        ),
        (
            "documento como inteiro",
            lambda logger: logger.info("evento", documento=52998224725),
            ["52998224725"],
        ),
        (
            "authorization igual a None",
            lambda logger: logger.info("evento authorization=None", authorization=None),
            [],
        ),
        (
            "set-cookie como lista",
            lambda logger: logger.info("evento", headers={"set-cookie": ["sid=abc123", "x=y"]}),
            ["abc123"],
        ),
    ],
)
def test_a_segunda_passada_nunca_quebra_o_json(nome: str, registrar, vazados: list[str]) -> None:  # type: ignore[no-untyped-def]
    linhas = _linhas("production", lambda: registrar(logging_setup.get_logger("teste")))
    assert len(linhas) == 1, nome
    registro = json.loads(linhas[0])
    assert registro["level"] in {"info", "error"}
    for vazado in vazados:
        assert vazado not in linhas[0], (nome, vazado)


def _excecao(logger, mensagem: str) -> None:  # type: ignore[no-untyped-def]
    try:
        raise RuntimeError(mensagem)
    except RuntimeError:
        logger.exception("falhou")


def test_valor_none_de_cabecalho_continua_none_e_lista_sai_inteira() -> None:
    linhas = _linhas(
        "production",
        lambda: logging_setup.get_logger("teste").info(
            "evento", authorization=None, headers={"set-cookie": ["sid=abc123"], "accept": "*/*"}
        ),
    )
    registro = json.loads(linhas[0])
    assert registro["authorization"] is None
    assert registro["headers"] == {"set-cookie": "<REDACTED>", "accept": "*/*"}


def test_linha_que_nao_decodifica_vira_linha_minima_valida() -> None:
    linha = logging_setup._redigir_a_linha_json(None, "exception", "nao e json {")
    assert json.loads(linha) == {
        "level": "error",
        "event": "log_line_unparseable",
        "request_id": None,
    }


@pytest.mark.parametrize(
    ("valor", "vazado"),
    [
        ("Authorization: Bearer abc123", "abc123"),
        ("proxy-authorization: Basic abc123", "abc123"),
        ("{ 'set-cookie': [ 'sid=abc123', 'tema=claro' ] }", "abc123"),
        ("Map(1) { 'authorization' => 'Bearer abc123' }", "abc123"),
        ("[(b'host', b'sell.test'), (b'authorization', b'Bearer abc123')]", "abc123"),
        ("Authorization: Bearer\n abc123\nproxima", "abc123"),
        ("ana@exémplo.com.br", "exémplo"),
        ("josé@gmail.com", "gmail"),
        ("/u/ana%40exemplo.com", "exemplo"),
        ("ana_silva@exemplo.com", "ana_"),
        ("GET /c/link?token=abc123 HTTP/1.1", "abc123"),
        ("https://sell.test/volta#rt=abc123", "abc123"),
        ("http://api.internal:8080?notify=alice@autonomia.site", "alice"),
    ],
)
def test_formatos_que_escapavam_saem_redigidos_na_linha(valor: str, vazado: str) -> None:
    linhas = _linhas("production", lambda: logging_setup.get_logger("teste").info("e", campo=valor))
    assert len(linhas) == 1
    assert vazado not in linhas[0]
    assert "<REDACTED>" in json.loads(linhas[0])["campo"]


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("http://api.internal:8080?notify=x", "http://api.internal:8080?<REDACTED>"),
        ("GET /c/link?token=abc123 HTTP", "GET /c/link?<REDACTED> HTTP"),
        ("https://h.test/a#rt=x fim", "https://h.test/a#<REDACTED> fim"),
        ("'set-cookie': [ 'a=1', 'b=2' ]", "'set-cookie': [<REDACTED>]"),
        ("(b'authorization', b'Bearer x')", "(b'authorization', b'<REDACTED>')"),
        ("'authorization' => 'Bearer x'", "'authorization' => '<REDACTED>'"),
        ('Authorization: Bearer x\n  File "/a.py"', 'Authorization: <REDACTED>\n  File "/a.py"'),
        (
            "Authorization: Bearer x\n    at f (a.js:1:2)",
            "Authorization: <REDACTED>\n    at f (a.js:1:2)",
        ),
        (
            "authorization, cookie e x-api-key faltaram",
            "authorization, cookie e x-api-key faltaram",
        ),
        ("Apartamento 50 m²", "Apartamento 50 m²"),
        ("pergunta?", "pergunta?"),
    ],
)
def test_regras_novas_do_redator(entrada: str, esperado: str) -> None:
    assert redigir_dado_pessoal(entrada) == esperado


def test_texto_grande_tem_teto_e_nao_deixa_email_pela_metade() -> None:
    texto = ("x " * 9000) + EMAIL + (" y" * 9000)
    limitado = limitar_tamanho(texto)
    assert len(limitado) <= TETO_DE_TEXTO
    assert limitar_tamanho(limitado) == limitado
    for pedaco in limitado.split():
        assert pedaco in {"x", "y", "[...truncated...]"}


@pytest.mark.parametrize(
    "entrada",
    [
        "authorization " * 3_000,
        "authorization:\n" * 3_000,
        "cookie" * 7_000,
        "http://a/b?c " * 4_000,
        "a@b.co " * 6_000,
        "'set-cookie': [" * 3_000,
        "1.2-3/" * 7_000,
    ],
)
def test_redigir_texto_grande_e_rapido(entrada: str) -> None:
    """38 KB levavam 26 s. Varredura única e teto antes de redigir."""
    inicio = time.perf_counter()
    linhas = _linhas(
        "production", lambda: logging_setup.get_logger("teste").info("e", campo=entrada)
    )
    assert time.perf_counter() - inicio < 2
    json.loads(linhas[0])
