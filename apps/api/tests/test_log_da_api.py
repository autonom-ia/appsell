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
import logging
import secrets
import warnings
from collections.abc import AsyncIterator, Iterator
from io import StringIO
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from httpx import ASGITransport, AsyncClient

from appsell_api import logging_setup

_ESPERA_DA_ROTA_LENTA = 0.05
_TOKENS_DA_AMOSTRA = 20_000


@pytest.fixture(autouse=True)
def _devolve_a_configuracao_da_aplicacao() -> Iterator[None]:
    """Nenhum teste deste arquivo pode vazar a própria configuração.

    Cada caso aponta o structlog para um `StringIO` privado. Sem esta devolução,
    todo log emitido depois deste arquivo — no mesmo processo do pytest — iria
    para um buffer morto e sumiria do stdout. É o mesmo defeito que motivou
    trocar `structlog.reset_defaults()` nos testes de `learning_routes`.
    """
    try:
        yield
    finally:
        logging_setup.configure_logging_from_environment()


def _configura(ambiente: str) -> StringIO:
    fluxo = StringIO()
    logging_setup.configure_logging(environment=ambiente, log_level="INFO", stream=fluxo)
    return fluxo


def _cliente(app: FastAPI) -> AsyncClient:
    """Cliente que reage à exceção como um servidor ASGI reagiria.

    `raise_app_exceptions=True` — o padrão do httpx — faria a exceção subir para
    o teste, escondendo o que o Starlette de fato responde ao usuário.
    """
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://test")


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
    """O redator pega o que **se parece** com segredo — não o que é segredo.

    Esta docstring já afirmou que "token, chave e senha nunca são impressos", e
    era falso: uma senha curta, um CPF ou um e-mail passam inteiros. O redator é
    rede de segurança para o descuido e para o traceback, cujo conteúdo ninguém
    escolhe. A regra continua sendo não colocar credencial nem dado pessoal em
    campo de log.

    O que ele cobre está em `logging_setup`: sequência longa de caracteres de
    token, base64 com `+`/`=`, credencial embutida em URL e número de cartão por
    Luhn. Perder o hash exato de um material custa uma consulta a mais no
    diagnóstico; imprimir um token custa uma rotação de credencial.
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

    async with _cliente(app) as client:
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

    async with _cliente(app) as client:
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

    async with _cliente(app) as client:
        resposta = await client.get("/v1/itens/42", headers={"X-Request-Id": "abc-123"})

    registro = json.loads(_linhas(fluxo)[0])
    assert registro["request_id"] == "abc-123"
    assert resposta.headers["x-request-id"] == "abc-123"


@pytest.mark.asyncio
async def test_identificador_de_correlacao_malformado_e_descartado_e_nao_ecoado() -> None:
    """Cabeçalho é dado de terceiro. Ecoar o que veio é injeção de log."""
    fluxo = _configura("production")
    app = _app_de_prova()

    async with _cliente(app) as client:
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

    async with _cliente(app) as client:
        resposta = await client.get("/v1/explode")

    assert resposta.status_code == 500
    assert "boom" not in resposta.text
    # O corpo continua sendo o do Starlette. Responder um JSON com `detail`
    # daqui mudava o codigo de erro que o BFF expoe: ele le `payload.detail`
    # como codigo, e "internal server error" — com espacos — tomaria o lugar de
    # `domain_api_error`.
    assert resposta.text == "Internal Server Error"

    eventos = [json.loads(linha) for linha in _linhas(fluxo)]
    assert [evento["event"] for evento in eventos] == ["unhandled_exception"]
    falha = eventos[0]
    assert falha["route"] == "/v1/explode"
    assert falha["method"] == "GET"
    assert falha["request_id"]
    assert falha["level"] == "error"
    assert isinstance(falha["duration_ms"], float)
    assert falha["response_started"] is False


@pytest.mark.asyncio
async def test_health_check_nao_polui_o_log() -> None:
    """O Swarm sonda `/health/*` a cada poucos segundos.

    Sem exclusão, o access log viraria milhares de linhas por dia de uma rota
    que não diz nada — e enterraria as linhas que importam. Foi metade do
    motivo de `--no-access-log` existir.
    """
    fluxo = _configura("production")
    app = _app_de_prova()

    async with _cliente(app) as client:
        await client.get("/health/live")
        await client.get("/health/ready")

    assert _linhas(fluxo) == []


@pytest.mark.asyncio
async def test_rota_desconhecida_e_registrada_sem_ecoar_o_caminho_pedido() -> None:
    """404 de varredura é sinal útil, mas o caminho é entrada de atacante."""
    fluxo = _configura("production")
    app = _app_de_prova()

    async with _cliente(app) as client:
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

    async with _cliente(app) as client:
        await client.get("/v1/lento")

    registro = json.loads(_linhas(fluxo)[0])
    assert registro["route"] == "/v1/lento"
    assert registro["duration_ms"] >= _ESPERA_DA_ROTA_LENTA * 1000 * 0.8


def test_o_log_sai_no_stderr_e_segue_a_saida_corrente() -> None:
    """Duas coisas de uma vez, e as duas quebraram algo real.

    O `stdout` é canal de protocolo: `pdf_verifier` roda o parser num
    subprocesso e faz `json.loads(completed.stdout)`, com teto de 1.024 bytes.
    Uma linha de log naquele processo quebrava a leitura de **todo** PDF — e a
    captura do `logging` da biblioteca padrão torna isso provável, porque basta
    um `WARNING` de qualquer dependência.

    E guardar o fluxo na configuração congela o destino: quem trocar a saída
    depois deixa de ver o log, sem que nada quebre nem avise.
    """
    logging_setup.configure_logging(environment="production", log_level="INFO")
    saida_de_erro = StringIO()
    saida_padrao = StringIO()

    with contextlib.redirect_stderr(saida_de_erro), contextlib.redirect_stdout(saida_padrao):
        logging_setup.get_logger("teste").info("evento_de_prova")
        logging.getLogger("uvicorn.error").warning("aviso da biblioteca")

    assert saida_padrao.getvalue() == ""
    eventos = [json.loads(linha) for linha in _linhas(saida_de_erro)]
    assert [evento["event"] for evento in eventos] == ["evento_de_prova", "aviso da biblioteca"]


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

    async with _cliente(app) as client:
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

    async with _cliente(app) as client:
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


@pytest.mark.asyncio
async def test_handler_global_de_excecao_registrado_pela_aplicacao_continua_rodando() -> None:
    """Registrar a exceção não pode ser o mesmo que consumi-la.

    A primeira versão respondia o 500 de dentro do middleware. Como ele fica
    *dentro* do `ServerErrorMiddleware`, a exceção nunca chegava ao Starlette:
    um handler registrado com `add_exception_handler(Exception, ...)` era
    ignorado em silêncio, e o modo debug parava de mostrar o traceback.
    """
    fluxo = _configura("production")
    app = FastAPI()
    logging_setup.install_request_logging(app)

    async def tratador(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse({"error": "tratado_pela_aplicacao"}, status_code=503)

    app.add_exception_handler(Exception, tratador)

    @app.get("/v1/explode")
    async def explode() -> dict[str, str]:
        raise RuntimeError("boom")

    async with _cliente(app) as client:
        resposta = await client.get("/v1/explode")

    assert resposta.status_code == 503
    assert resposta.json() == {"error": "tratado_pela_aplicacao"}
    eventos = [json.loads(linha) for linha in _linhas(fluxo)]
    assert any(evento["event"] == "unhandled_exception" for evento in eventos)


@pytest.mark.asyncio
async def test_falha_depois_do_inicio_da_resposta_nao_deixa_o_ciclo_asgi_pela_metade() -> None:
    """Uma resposta em fluxo que quebra no meio precisa continuar quebrando.

    Engolir a exceção depois de `http.response.start` deixava o servidor sem o
    `http.response.body` final — o transporte ASGI falhava com `AssertionError`
    em vez de propagar o erro real. Esta API entrega PDF e vídeo, então o
    caminho de fluxo não é hipotético.
    """
    fluxo = _configura("production")
    app = FastAPI()
    logging_setup.install_request_logging(app)

    async def pedacos() -> AsyncIterator[bytes]:
        yield b"comeco"
        raise RuntimeError("quebrou no meio")

    @app.get("/v1/fluxo")
    async def fluxo_de_bytes() -> StreamingResponse:
        return StreamingResponse(pedacos(), media_type="application/octet-stream")

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="quebrou no meio"):
            await client.get("/v1/fluxo")

    eventos = [json.loads(linha) for linha in _linhas(fluxo)]
    falha = next(evento for evento in eventos if evento["event"] == "unhandled_exception")
    assert falha["response_started"] is True
    assert falha["route"] == "/v1/fluxo"


def test_credencial_embutida_em_url_de_conexao_nao_chega_ao_log() -> None:
    """O caso que o `[A-Za-z0-9_-]{32,}` não pegava, e que mais dói.

    `postgresql://alice:p@ssw0rd@db.internal/prod` não tem nenhuma sequência
    longa: a senha é curta e o `@` e o `:` quebram qualquer corrida. E este é
    justamente o texto que aparece no traceback de uma falha de driver — que
    agora vai para o log, porque a exceção passou a ser registrada.
    """
    fluxo = _configura("production")

    logging_setup.get_logger("teste").info(
        "evento_de_prova",
        dsn="postgresql://alice:p@ssw0rd@db.internal:5432/prod",
        cache="redis://:outra_senha@cache.internal:6379/0",
    )

    linha = _linhas(fluxo)[0]
    assert "alice" not in linha
    # A senha contém `@`. Parar no primeiro deixava `ssw0rd` visível, e um
    # usuário vazio como o do Redis não era reconhecido como credencial.
    assert "p@ssw0rd" not in linha
    assert "ssw0rd" not in linha
    assert "outra_senha" not in linha
    assert "cache.internal" in linha
    # O host e o banco sobrevivem: sem eles a linha nao diz a qual conexao o
    # erro se refere, e o redator teria trocado um problema por outro.
    assert "db.internal" in linha
    assert "prod" in linha


def test_segredo_em_base64_nao_chega_ao_log_e_caminho_de_traceback_sobrevive() -> None:
    """`+`, `/` e `=` quebram a corrida de `[A-Za-z0-9_-]` em pedaços curtos.

    O `=` só conta como padding **no fim**. Aceitá-lo em qualquer posição fazia
    `correlation=abcdefghijklmnop` desaparecer inteiro; exigir apenas `+` fazia
    um caminho com diretório `C++` perder o começo. Caminho de arquivo aparece
    em todo traceback, e redigi-lo apagaria a parte que diz onde o erro
    aconteceu.
    """
    fluxo = _configura("production")
    # Os `+` e `/` quebram este valor em trechos de quatro caracteres, e nenhum
    # deles chega perto dos 32 que o padrao de tamanho exige. Sem o padrao de
    # base64, o segredo inteiro saía no log.
    segredo = "YWJj+ZGVm/Z2hp+amts/bW5v+cHFy/c3R1+dnd4/eXow+MTIz/NDU2="

    logging_setup.get_logger("teste").info("evento_de_prova", chave=segredo)

    assert segredo not in _linhas(fluxo)[0]
    assert "<REDACTED>" in _linhas(fluxo)[0]


def test_caminho_de_arquivo_do_traceback_nao_e_confundido_com_base64() -> None:
    fluxo = _configura("production")
    caminho = "/Users/alguem/dev/worktrees/appsell/apps/api/src/appsell_api/main.py"

    logging_setup.get_logger("teste").info("evento_de_prova", arquivo=caminho)

    assert caminho in _linhas(fluxo)[0]


def test_numero_de_cartao_nao_chega_ao_log_e_identificador_numerico_sobrevive() -> None:
    """Cartão é redigido por Luhn, não por tamanho.

    Redigir todo número comprido apagaria identificador de pedido e de asset —
    e um log sem identificador não serve para nada. O dígito verificador separa
    os dois casos sem chute.
    """
    fluxo = _configura("production")

    logging_setup.get_logger("teste").info(
        "evento_de_prova",
        cartao="4111 1111 1111 1111",
        outro_cartao="4111111111111111",
        pedido="1234567890123456789",
    )

    linha = _linhas(fluxo)[0]
    assert "4111" not in linha
    assert "1234567890123456789" in linha


def test_o_log_da_biblioteca_padrao_passa_pelos_mesmos_processadores() -> None:
    """Sem isto o redator é contornado no caso mais perigoso.

    Medido contra o uvicorn real: uma exceção não tratada saía **duas vezes** —
    a linha estruturada, com a credencial redigida, e logo abaixo o traceback
    que o uvicorn imprime pelo `logging` padrão, com
    `postgresql://alice:p@ss@db.internal/prod` em claro. Registrar a exceção e
    re-levantá-la só é seguro se o que o servidor registra depois também passar
    pelo redator.

    De quebra, uvicorn, SQLAlchemy e boto3 passam a sair no mesmo formato.
    """
    fluxo = _configura("production")

    try:
        raise RuntimeError("postgresql://alice:p@ss@db.internal/prod")
    except RuntimeError:
        logging.getLogger("uvicorn.error").exception("erro no servidor")

    linha = _linhas(fluxo)[0]
    assert "alice:p@ss" not in linha
    registro = json.loads(linha)
    assert registro["event"] == "erro no servidor"
    assert registro["level"] == "error"
    assert "<REDACTED>" in registro["exception"]


def test_o_uvicorn_nao_guarda_tratador_proprio_depois_da_configuracao() -> None:
    """Tratador próprio significa uma cópia crua ao lado da linha redigida."""
    logging.getLogger("uvicorn.error").addHandler(logging.StreamHandler())

    _configura("production")

    for nome in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger_da_biblioteca = logging.getLogger(nome)
        assert logger_da_biblioteca.handlers == []
        assert logger_da_biblioteca.propagate is True


@pytest.mark.parametrize(
    ("nome", "valor"),
    [
        ("uuid", "550e8400-e29b-41d4-a716-446655440000"),
        ("chave com uuid", "derivatives/pdf-link-video/550e8400-e29b-41d4-a716-446655440000.mp4"),
        ("slug de produto", "curso-de-inteligencia-artificial-na-pratica-2026"),
        ("nome de banco com sublinhado", "appsell_native_90-backup-2026-08-31"),
        ("query com e-mail", "http://api.internal:8080?notify=ops@autonomia.site"),
        ("igual no meio", "correlation=abcdefghijklmnop"),
        ("caminho com C++", "/Users/alguem/dev/C++/worktrees/appsell_api/main.py"),
        ("carimbo unix", "1788134400000"),
        ("zeros que passam por Luhn", "0000000000000"),
        ("identificador de pedido", "1234567890123456789"),
        ("carimbo ISO", "2026-08-31T16:15:36.303059Z"),
    ],
)
def test_dado_de_diagnostico_sobrevive_ao_redator(nome: str, valor: str) -> None:
    """Redigir demais é a outra forma de não ter log.

    Cada caso aqui foi apagado por alguma versão anterior do redator: o UUID e o
    slug pelo padrão de tamanho, `correlation=abcdefghijklmnop` e o caminho com
    `C++` pelo padrão de base64, o carimbo Unix e a sequência de zeros por Luhn
    sozinho, e o host da URL por um casamento que atravessava o `?`.
    """
    fluxo = _configura("production")

    logging_setup.get_logger("teste").info("evento_de_prova", campo=valor)

    assert json.loads(_linhas(fluxo)[0])["campo"] == valor, nome


@pytest.mark.parametrize(
    ("nome", "valor"),
    [
        ("token com prefixo", "sk-a1B2c3D4a1B2c3D4a1B2c3D4a1B2c3D4a1B2c3D4"),
        ("hex de 40", "a3f5b8c92d1e4f7a6b0c3d8e5f2a9b4c7d1e6f3a"),
        ("base64url com separador", "eyJhbGciOiJIUzI1NiJ9-QWxndW1Db2lzYUxvbmdhQXF1aQ_MaisUmPouco"),
        ("base64 com padding", "U2VjcmV0VG9rZW5EZUV4ZW1wbG9NdWl0b0xvbmdvQXF1aQ=="),
        ("cartao Visa", "4111 1111 1111 1111"),
        ("cartao Amex", "378282246310005"),
        ("cartao Mastercard", "5555555555554444"),
        ("cartao Discover na faixa 644", "6445641386133871"),
        ("cartao UnionPay", "6221260000000000"),
        ("cartao JCB", "3530111333300000"),
        ("cartao Diners", "30569309025904"),
        ("token urlsafe sem trecho longo", "nB2iJc4dJ-x_z6VZwp2OQ9G_gjOyE-FwiLg9LsbHBzY"),
    ],
)
def test_o_que_parece_segredo_nao_sobrevive_ao_redator(nome: str, valor: str) -> None:
    fluxo = _configura("production")

    logging_setup.get_logger("teste").info("evento_de_prova", campo=valor)

    assert valor not in _linhas(fluxo)[0], nome


def test_o_cliente_http_interno_nao_registra_a_url_de_cada_chamada() -> None:
    """Capturar a raiz em INFO trouxe junto o log de cliente do `httpx`.

    Ele registra `HTTP Request: GET <url completa>` a cada chamada, e a API fala
    com o sandbox e com o identity por `httpx` — a URL inteira, com a query,
    iria para o log a cada requisição interna. Ruído e risco ao mesmo tempo.
    """
    fluxo = _configura("production")

    logging.getLogger("httpx").info("HTTP Request: GET http://interno/v1/x?token=abc")
    logging.getLogger("botocore").info("assinando requisicao")
    logging.getLogger("httpx").warning("o cliente falhou")

    eventos = [json.loads(linha) for linha in _linhas(fluxo)]
    assert [evento["event"] for evento in eventos] == ["o cliente falhou"]


@pytest.mark.asyncio
async def test_a_duracao_do_evento_de_excecao_e_a_duracao_real() -> None:
    """A exceção não emite `http_request`, então ela carrega a própria duração.

    Sem esta asserção, `duration_ms=0.0` no caminho de falha passaria por
    qualquer verificação de tipo — e é justamente na falha que a pergunta "a
    rota estava lenta?" costuma importar.
    """
    fluxo = _configura("production")
    app = FastAPI()
    logging_setup.install_request_logging(app)

    @app.get("/v1/lento_e_explode")
    async def lento_e_explode() -> dict[str, str]:
        await asyncio.sleep(_ESPERA_DA_ROTA_LENTA)
        raise RuntimeError("boom")

    async with _cliente(app) as client:
        await client.get("/v1/lento_e_explode")

    falha = json.loads(_linhas(fluxo)[0])
    assert falha["event"] == "unhandled_exception"
    assert falha["duration_ms"] >= _ESPERA_DA_ROTA_LENTA * 1000 * 0.8


def test_numero_com_prefixo_de_bandeira_que_falha_no_luhn_e_preservado() -> None:
    """O dígito verificador ainda decide, e a escolha é consciente.

    Exigir só o prefixo apagaria qualquer identificador de 13 a 19 dígitos que
    por acaso comece com `4`. Exigir só Luhn apagaria cerca de um em cada dez
    identificadores numéricos — o carimbo Unix `1788134400000` passa no
    checksum. Os dois juntos: cartão real sempre passa por Luhn, e um número que
    tem a cara de cartão mas falha no dígito não é um cartão válido vazando.
    """
    fluxo = _configura("production")
    quase_cartao = "4111111111111112"

    logging_setup.get_logger("teste").info("evento_de_prova", campo=quase_cartao)

    assert json.loads(_linhas(fluxo)[0])["campo"] == quase_cartao


def test_nenhum_token_gerado_pelo_sistema_escapa_do_redator() -> None:
    """O sistema gera `secrets.token_urlsafe(32)` em `platform_routes`.

    A primeira versão do discriminante de identificador olhava só o tamanho do
    maior trecho entre `-` e `_`. Medido sobre 100.000 tokens reais, **5.027**
    passavam intactos — 5% — porque por acaso não tinham nenhum trecho longo.
    Exemplo que escapava: `nB2iJc4dJ-x_z6VZwp2OQ9G_gjOyE-FwiLg9LsbHBzY`.

    O segundo critério é a ausência de maiúscula: o `_SLUG` de `routes.py` só
    aceita minúscula, dígito e hífen, e um token só escaparia agora se sorteasse
    43 caracteres sem uma única maiúscula.
    """
    escaparam = [
        token
        for token in (secrets.token_urlsafe(32) for _ in range(_TOKENS_DA_AMOSTRA))
        if logging_setup._redigir_texto(token) == token
    ]

    assert escaparam == []


@pytest.mark.parametrize("ambiente", ["production", "local", "ci", "staging"])
def test_a_credencial_do_traceback_e_redigida_em_todo_ambiente(ambiente: str) -> None:
    """Em `local`/`ci` o traceback era montado depois do redator.

    O `ConsoleRenderer` formata a exceção sozinho, então `format_exc_info` fica
    fora da cadeia nesses ambientes e o redator estrutural só via
    `exc_info=True`. Resultado: `RuntimeError("postgresql://alice:p@ssw0rd@…")`
    saía com usuário e senha inteiros — e num run de CI isso fica gravado no log
    público do GitHub. A redação passa a ter uma passada final sobre a linha já
    montada.
    """
    fluxo = _configura(ambiente)

    try:
        raise RuntimeError("postgresql://alice:p@ssw0rd@db.internal/prod")
    except RuntimeError:
        logging_setup.get_logger("teste").exception("falhou")

    saida = fluxo.getvalue()
    assert "alice" not in saida
    assert "p@ssw0rd" not in saida
    assert "<REDACTED>" in saida
    assert "db.internal" in saida


@pytest.mark.parametrize("ambiente", ["production", "local"])
def test_a_credencial_do_logging_da_biblioteca_padrao_e_redigida_em_todo_ambiente(
    ambiente: str,
) -> None:
    fluxo = _configura(ambiente)

    try:
        raise RuntimeError("postgresql://alice:p@ssw0rd@db.internal/prod")
    except RuntimeError:
        logging.getLogger("uvicorn.error").exception("erro no servidor")

    saida = fluxo.getvalue()
    assert "alice" not in saida
    assert "p@ssw0rd" not in saida
