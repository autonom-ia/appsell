"""O log da API: o que ele grava, o que ele esconde e o que ele não polui.

Em 31/08/2026 a API de produção gravou **22 linhas em 24 horas**. Vinte delas
eram `Started server process` e `Shutting down` do uvicorn. Duas eram da
aplicação. O `CMD` do contêiner passava `--no-access-log`, `structlog.configure`
não existia em lugar nenhum do repositório e não havia handler de exceção
global. Toda requisição, todo 500 e todo erro de negócio saíam do processo sem
deixar rastro, e cada diagnóstico custava horas reproduzindo o processo real
dentro do contêiner.

Estes testes prendem a fundação: renderizador por ambiente, redação de segredo,
uma linha por requisição, identificador de correlação, exceção com contexto, e
o silêncio dos health checks — que o Swarm chama a cada poucos segundos e que,
sem exclusão, afogariam o log em milhares de linhas por dia.
"""

import asyncio
import contextlib
import json
import warnings
from io import StringIO
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from appsell_api import logging_setup

_ESPERA_DA_ROTA_LENTA = 0.05


def _configura(ambiente: str) -> StringIO:
    fluxo = StringIO()
    logging_setup.configure_logging(environment=ambiente, log_level="INFO", stream=fluxo)
    return fluxo


def _linhas(fluxo: StringIO) -> list[str]:
    return [linha for linha in fluxo.getvalue().splitlines() if linha.strip()]


def test_producao_renderiza_uma_linha_json_por_evento() -> None:
    """Log de produção precisa ser parseável por máquina.

    O renderizador padrão do structlog é o de console — `key=value` com
    alinhamento e cor. É o que estava saindo em produção, porque ninguém nunca
    chamou `structlog.configure`.
    """
    fluxo = _configura("production")

    logging_setup.get_logger("teste").info("evento_de_prova", seller="autonomia", total=3)

    linhas = _linhas(fluxo)
    assert len(linhas) == 1
    registro = json.loads(linhas[0])
    assert registro["event"] == "evento_de_prova"
    assert registro["level"] == "info"
    assert registro["seller"] == "autonomia"
    assert registro["total"] == 3
    assert "timestamp" in registro


def test_local_renderiza_console_legivel_em_vez_de_json() -> None:
    fluxo = _configura("local")

    logging_setup.get_logger("teste").info("evento_de_prova", seller="autonomia")

    saida = _linhas(fluxo)[0]
    assert "evento_de_prova" in saida
    with pytest.raises(json.JSONDecodeError):
        json.loads(saida)


def test_valor_longo_o_bastante_para_ser_segredo_nao_chega_ao_log() -> None:
    """Regra que não cede: token, chave e senha nunca são impressos.

    O redator não sabe distinguir um token de um hash de conteúdo, e por isso
    trata os dois como segredo. É uma troca consciente: perder o hash exato de
    um material custa uma consulta a mais no diagnóstico; imprimir um token
    custa uma rotação de credencial.
    """
    fluxo = _configura("production")
    token = "sk-" + "a1B2c3D4" * 5

    logging_setup.get_logger("teste").info("evento_de_prova", authorization=f"Bearer {token}")

    registro = json.loads(_linhas(fluxo)[0])
    assert token not in _linhas(fluxo)[0]
    assert "<REDACTED>" in registro["authorization"]


def test_redacao_alcanca_valor_aninhado_em_lista_e_dicionario() -> None:
    fluxo = _configura("production")
    segredo = "Z" * 40

    logging_setup.get_logger("teste").info(
        "evento_de_prova", contexto={"itens": [segredo], "seguro": "ok"}
    )

    linha = _linhas(fluxo)[0]
    assert segredo not in linha
    assert "ok" in linha


def test_valor_curto_continua_legivel() -> None:
    """Redigir demais é tão ruim quanto não redigir: o log deixa de servir."""
    fluxo = _configura("production")

    logging_setup.get_logger("teste").info(
        "evento_de_prova", seller_slug="autonomia", status=404, rota="/v1/catalog"
    )

    registro = json.loads(_linhas(fluxo)[0])
    assert registro["seller_slug"] == "autonomia"
    assert registro["rota"] == "/v1/catalog"
    assert registro["status"] == 404


@pytest.mark.asyncio
async def test_cada_requisicao_deixa_uma_linha_com_rota_status_e_duracao() -> None:
    fluxo = _configura("production")
    app = _app_de_prova()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resposta = await client.get("/v1/itens/42")

    assert resposta.status_code == 200
    registro = json.loads(_linhas(fluxo)[0])
    assert registro["event"] == "http_request"
    assert registro["method"] == "GET"
    assert registro["route"] == "/v1/itens/{item_id}"
    assert registro["status"] == 200
    assert isinstance(registro["duration_ms"], float)
    assert registro["request_id"]


@pytest.mark.asyncio
async def test_a_linha_da_requisicao_nao_carrega_query_nem_cabecalho_de_autorizacao() -> None:
    """O caminho bruto vaza identificador e a query vaza token de acesso.

    Por isso a linha grava o **template** da rota, nunca o caminho concreto nem
    a query string, e nenhum cabeçalho entra no evento.
    """
    fluxo = _configura("production")
    app = _app_de_prova()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get(
            "/v1/itens/42",
            params={"token": "T" * 40},
            headers={"Authorization": "Bearer " + "S" * 40},
        )

    linha = _linhas(fluxo)[0]
    assert "T" * 40 not in linha
    assert "S" * 40 not in linha
    assert "/v1/itens/42" not in linha
    registro = json.loads(linha)
    assert "authorization" not in {chave.lower() for chave in registro}


@pytest.mark.asyncio
async def test_o_identificador_de_correlacao_vem_do_cabecalho_quando_ele_e_valido() -> None:
    fluxo = _configura("production")
    app = _app_de_prova()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resposta = await client.get("/v1/itens/42", headers={"X-Request-Id": "abc-123"})

    registro = json.loads(_linhas(fluxo)[0])
    assert registro["request_id"] == "abc-123"
    assert resposta.headers["x-request-id"] == "abc-123"


@pytest.mark.asyncio
async def test_identificador_de_correlacao_malformado_e_descartado_e_nao_ecoado() -> None:
    """Cabeçalho é dado de terceiro. Ecoar o que veio é injeção de log."""
    fluxo = _configura("production")
    app = _app_de_prova()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resposta = await client.get(
            "/v1/itens/42", headers={"X-Request-Id": 'evento" injetado\n outra linha'}
        )

    linhas = _linhas(fluxo)
    assert len(linhas) == 1
    registro = json.loads(linhas[0])
    assert registro["request_id"] != 'evento" injetado\n outra linha'
    assert registro["request_id"]
    assert "injetado" not in resposta.headers["x-request-id"]


@pytest.mark.asyncio
async def test_excecao_nao_tratada_grava_contexto_da_rota_e_responde_500_generico() -> None:
    """O 500 mudo era o pior caso: erro em produção sem rota, sem motivo.

    A resposta continua genérica — o cliente não recebe traceback — mas o log
    passa a ter rota, método e identificador para cruzar com a requisição.
    """
    fluxo = _configura("production")
    app = _app_de_prova()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resposta = await client.get("/v1/explode")

    assert resposta.status_code == 500
    assert "boom" not in resposta.text

    eventos = [json.loads(linha) for linha in _linhas(fluxo)]
    falha = next(evento for evento in eventos if evento["event"] == "unhandled_exception")
    assert falha["route"] == "/v1/explode"
    assert falha["method"] == "GET"
    assert falha["request_id"]
    assert falha["level"] == "error"

    requisicao = next(evento for evento in eventos if evento["event"] == "http_request")
    assert requisicao["status"] == 500
    assert requisicao["request_id"] == falha["request_id"]


@pytest.mark.asyncio
async def test_health_check_nao_polui_o_log() -> None:
    """O Swarm sonda `/health/*` a cada poucos segundos.

    Sem exclusão, o access log viraria milhares de linhas por dia de uma rota
    que não diz nada — e enterraria as linhas que importam. Foi metade do
    motivo de `--no-access-log` existir.
    """
    fluxo = _configura("production")
    app = _app_de_prova()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/health/live")
        await client.get("/health/ready")

    assert _linhas(fluxo) == []


@pytest.mark.asyncio
async def test_rota_desconhecida_e_registrada_sem_ecoar_o_caminho_pedido() -> None:
    """404 de varredura é sinal útil, mas o caminho é entrada de atacante."""
    fluxo = _configura("production")
    app = _app_de_prova()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/wp-admin/../etc/passwd")

    registro = json.loads(_linhas(fluxo)[0])
    assert registro["status"] == 404
    assert registro["route"] == "<unmatched>"
    assert "passwd" not in _linhas(fluxo)[0]


def _app_de_prova() -> FastAPI:
    app = FastAPI()
    logging_setup.install_request_logging(app)

    @app.get("/v1/itens/{item_id}")
    async def item(item_id: str) -> dict[str, str]:
        return {"item": item_id}

    @app.get("/v1/lento")
    async def lento() -> dict[str, str]:
        await asyncio.sleep(_ESPERA_DA_ROTA_LENTA)
        return {"status": "ok"}

    @app.get("/v1/explode")
    async def explode() -> dict[str, str]:
        raise RuntimeError("boom")

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/explode")
    async def health_explode() -> dict[str, str]:
        raise RuntimeError("o banco caiu")

    return app


def test_em_producao_a_excecao_vira_campo_de_texto_no_json() -> None:
    fluxo = _configura("production")

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logging_setup.get_logger("teste").exception("falhou")

    registro = json.loads(_linhas(fluxo)[0])
    assert registro["level"] == "error"
    assert "RuntimeError: boom" in registro["exception"]


def test_em_local_a_excecao_sai_legivel_e_sem_aviso_do_renderizador() -> None:
    """O `ConsoleRenderer` formata a exceção sozinho.

    Deixar `format_exc_info` na cadeia em local fazia o structlog emitir
    `UserWarning: Remove format_exc_info from your processor chain` a cada
    exceção registrada — e a suíte inteira passava mesmo assim, porque ninguém
    olhava o aviso.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        fluxo = _configura("local")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            logging_setup.get_logger("teste").exception("falhou")

    saida = fluxo.getvalue()
    assert "falhou" in saida
    assert "RuntimeError" in saida
    assert "boom" in saida


@pytest.mark.asyncio
async def test_a_duracao_registrada_e_a_duracao_real_da_requisicao() -> None:
    """`duration_ms` é o campo que responde "a rota está lenta?".

    Um campo presente mas sempre zero passaria por qualquer verificação de tipo
    e mentiria exatamente na pergunta para a qual ele existe.
    """
    fluxo = _configura("production")
    app = _app_de_prova()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/v1/lento")

    registro = json.loads(_linhas(fluxo)[0])
    assert registro["route"] == "/v1/lento"
    assert registro["duration_ms"] >= _ESPERA_DA_ROTA_LENTA * 1000 * 0.8


def test_o_log_segue_a_saida_corrente_e_nao_a_do_momento_da_configuracao() -> None:
    """Guardar `sys.stdout` na configuração congela o destino do log.

    O sintoma é o pior possível: nada quebra, nada avisa, e o log simplesmente
    não está onde deveria estar.
    """
    logging_setup.configure_logging(environment="production", log_level="INFO")
    fluxo = StringIO()

    with contextlib.redirect_stdout(fluxo):
        logging_setup.get_logger("teste").info("evento_de_prova")

    registro = json.loads(_linhas(fluxo)[0])
    assert registro["event"] == "evento_de_prova"


def test_o_campo_exception_pertence_ao_traceback_e_nao_a_dado_de_negocio() -> None:
    """`exception` é nome reservado do structlog.

    Em `learning_routes` ele carregava o nome da classe do erro, e o
    renderizador tratava esse texto como traceback — avisando a cada chamada e
    ocupando o campo onde o traceback real deveria estar. O dado de negócio
    passou a se chamar `exception_type`.
    """
    fluxo = _configura("production")

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logging_setup.get_logger("teste").exception("falhou", exception_type="RuntimeError")

    registro = json.loads(_linhas(fluxo)[0])
    assert registro["exception_type"] == "RuntimeError"
    assert "Traceback" in registro["exception"]


@pytest.mark.asyncio
async def test_o_identificador_de_correlacao_sobrevive_ao_redator() -> None:
    """O redator apaga sequências de 32 caracteres ou mais.

    Um `uuid4().hex` tem exatamente 32, e o identificador gerado saía do log
    como `<REDACTED>` — destruindo a correlação justamente no caso em que ela
    é a única forma de cruzar a linha da requisição com a linha do erro. Um
    identificador recebido pelo cabeçalho corre o mesmo risco, e por isso o
    formato aceito é mais curto que o limite do redator.
    """
    fluxo = _configura("production")
    app = _app_de_prova()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/v1/itens/42")
        await client.get("/v1/itens/42", headers={"X-Request-Id": "c" * 31})
        await client.get("/v1/itens/42", headers={"X-Request-Id": "d" * 32})

    gerado, recebido, longo_demais = (json.loads(linha) for linha in _linhas(fluxo))
    assert gerado["request_id"] != "<REDACTED>"
    assert len(gerado["request_id"]) < 32
    assert recebido["request_id"] == "c" * 31
    assert longo_demais["request_id"] not in {"<REDACTED>", "d" * 32}


@pytest.mark.asyncio
async def test_excecao_no_health_check_e_registrada_apesar_do_silencio_de_rotina() -> None:
    """O silêncio dos health checks vale para a rotina, não para a falha.

    A primeira versão desta fundação calava os dois: `/health/ready` levantando
    exceção devolvia 500 sem uma única linha de log — pior que antes, quando o
    `ServerErrorMiddleware` do Starlette ao menos imprimia o traceback no
    stderr. É o endpoint que decide se o Swarm mata a task, e é o mesmo defeito
    do incidente em que o `healthz` respondeu `pdf_sandbox: true` com o socket
    fora.
    """
    fluxo = _configura("production")
    app = _app_de_prova()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/health/live")
        resposta = await client.get("/health/explode")

    assert resposta.status_code == 500
    eventos = [json.loads(linha) for linha in _linhas(fluxo)]
    assert [evento["event"] for evento in eventos] == ["unhandled_exception"]
    assert eventos[0]["route"] == "/health/explode"
    assert eventos[0]["request_id"]


def test_estrutura_ciclica_nao_derruba_quem_registrou_o_evento() -> None:
    """Registrar um evento não pode quebrar quem o registrou.

    O redator descia em dict/list/tuple sem limite, e uma estrutura cíclica
    levava a `RecursionError` — que escapava da chamada de log direto para o
    handler da requisição.
    """
    fluxo = _configura("production")
    ciclico: dict[str, Any] = {}
    ciclico["eu"] = ciclico

    logging_setup.get_logger("teste").info("evento_de_prova", ciclo=ciclico)

    registro = json.loads(_linhas(fluxo)[0])
    assert registro["event"] == "evento_de_prova"
    assert "<too-deep>" in json.dumps(registro["ciclo"])


def test_estrutura_aninhada_dentro_do_limite_continua_inteira() -> None:
    """Cortar fundo demais esconde o dado; o limite existe só contra o ciclo."""
    fluxo = _configura("production")
    segredo = "Z" * 40
    aninhado = {"a": {"b": {"c": {"d": {"e": [segredo, "visivel"]}}}}}

    logging_setup.get_logger("teste").info("evento_de_prova", contexto=aninhado)

    linha = _linhas(fluxo)[0]
    assert segredo not in linha
    assert "visivel" in linha
    assert "<too-deep>" not in linha
