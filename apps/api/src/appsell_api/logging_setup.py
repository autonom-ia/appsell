"""Fundação de log estruturado da API.

Até 31/08/2026 a API de produção não chamava `structlog.configure` em lugar
nenhum, subia com `--no-access-log` e não tinha handler de exceção global. O
resultado medido foram 22 linhas em 24 horas, das quais 20 eram ciclo de vida
do uvicorn. Este módulo estabelece o mínimo para que um diagnóstico não exija
reproduzir o processo real dentro do contêiner:

- renderizador JSON em `staging`/`production`, console legível em `local`/`ci`;
- redação de qualquer valor comprido o bastante para ser um segredo;
- uma linha por requisição, com o **template** da rota — nunca o caminho
  concreto, a query string ou cabeçalhos;
- identificador de correlação por requisição, ecoado no `X-Request-Id`;
- exceção não tratada gravada com contexto e **re-levantada**, para o
  Starlette seguir respondendo o 500 como sempre respondeu;
- silêncio nos health checks, que o Swarm sonda a cada poucos segundos.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
import uuid
from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any, TextIO, cast

import structlog
from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "configure_logging",
    "configure_logging_from_environment",
    "get_logger",
    "install_request_logging",
    "request_id",
]

# O redator e uma rede de seguranca, **nao uma garantia**. Ele pega o que se
# parece com segredo; ele nao sabe o que e segredo. A regra continua sendo nao
# colocar credencial nem dado pessoal em campo de log — os padroes abaixo
# existem para o caso em que alguem errar, e para o traceback, que ninguem
# escolhe o conteudo.
_REDIGIDO = "<REDACTED>"

# 1. Sequencia longa de caracteres de token. Nao distingue um token de um hash
#    de conteudo, e trata os dois como segredo de proposito: perder o hash de um
#    material custa uma consulta a mais no diagnostico, imprimir um token custa
#    uma rotacao de credencial.
_SEGREDO = re.compile(r"[A-Za-z0-9_\-]{32,}")

# 2. Segredo em base64 padrao. `+`, `/` e `=` quebram a corrida do padrao acima
#    em pedacos curtos, e o valor inteiro passava.
#
#    O casamento exige `+` ou `=` — nao basta `/`. Caminho de arquivo e cheio de
#    `/` e aparece em todo traceback: `/Users/alguem/dev/worktrees` tem 27
#    caracteres do alfabeto base64 e viraria `<REDACTED>`, apagando justamente a
#    parte do traceback que diz onde o erro aconteceu. Um segredo base64 que nao
#    tenha nem `+` nem `=` escapa deste padrao; e um limite conhecido, nao um
#    descuido.
_BASE64 = re.compile(r"[A-Za-z0-9+/=]{24,}")
_MARCAS_DE_BASE64 = ("+", "=")

# 3. Credencial embutida em URL de conexao. Este e o caso que mais dooi e o que
#    o padrao de tamanho nunca pegaria: em
#    `postgresql://alice:p@ssw0rd@db.internal/prod` a senha e curta e os
#    separadores quebram qualquer corrida. E e exatamente o texto que aparece no
#    traceback de uma falha de driver. So a credencial e apagada; host, porta e
#    banco sobrevivem, senao a linha deixa de dizer a qual conexao o erro se
#    refere.
#    Os dois `[^/\s]*` sao gulosos de proposito: uma senha pode conter `@`, e
#    parar no primeiro deixava o resto dela visivel — `p@ssw0rd` virava
#    `<REDACTED>@ssw0rd`. Guloso, o casamento vai ate o ultimo `@` da
#    autoridade, e um usuario vazio (`redis://:senha@cache`) tambem entra.
_CREDENCIAL_EM_URL = re.compile(r"(?<=://)[^/\s]*:[^/\s]*(?=@)")

# 4. Numero de cartao. Redigir por tamanho apagaria identificador de pedido e de
#    asset; o digito verificador de Luhn separa os dois casos sem chute.
_CARTAO_CANDIDATO = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")

# Cabecalho e dado de terceiro: so um identificador estreito e aceito e ecoado.
# Sem isso, um valor com quebra de linha injeta um evento falso no log.
#
# O limite de 31 caracteres nao e estetico: o redator apaga qualquer sequencia
# de 32 ou mais, e um identificador de correlacao redigido perde exatamente a
# funcao que tem — cruzar a linha da requisicao com a linha do erro.
_TAMANHO_MAXIMO_DO_ID = 31
_ID_DE_CORRELACAO = re.compile(rf"^[A-Za-z0-9._-]{{1,{_TAMANHO_MAXIMO_DO_ID}}}$")
_DIGITOS_DO_ID_GERADO = 16

_ROTA_NAO_CASADA = "<unmatched>"
_PROFUNDIDADE_MAXIMA = 8
_PROFUNDO_DEMAIS = "<too-deep>"
_PREFIXO_DE_HEALTH = "/health/"

_AMBIENTES_ESTRUTURADOS = frozenset({"staging", "production"})


def configure_logging(
    *,
    environment: str,
    log_level: str = "INFO",
    stream: TextIO | None = None,
) -> None:
    """Instala a configuração global do structlog.

    `stream` existe para o teste conseguir ler o que foi renderizado de verdade,
    em vez de confiar no que o structlog diz que teria renderizado.
    """
    estruturado = environment in _AMBIENTES_ESTRUTURADOS
    renderizador: Any = (
        structlog.processors.JSONRenderer()
        if estruturado
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    processadores: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    if estruturado:
        # O `ConsoleRenderer` formata a excecao sozinho e avisa quando encontra
        # o traceback ja transformado em texto. So o renderizador JSON precisa
        # desta etapa.
        processadores.append(structlog.processors.format_exc_info)
    processadores += [_redigir_segredos, renderizador]
    structlog.configure(
        processors=processadores,
        logger_factory=structlog.WriteLoggerFactory(file=stream or _saida_padrao_corrente()),
        wrapper_class=structlog.make_filtering_bound_logger(_nivel(log_level)),
        cache_logger_on_first_use=False,
    )


def configure_logging_from_environment() -> None:
    """Reaplica a configuração da aplicação lendo o ambiente.

    Existe para quem precisa trocar a configuração do structlog por um instante
    — um teste que captura eventos, por exemplo — e devolvê-la depois.
    `structlog.reset_defaults()` **não** serve para isso: ele restaura o padrão
    da biblioteca, não o desta aplicação, e desliga em silêncio o renderizador
    JSON e o redator de segredos.
    """
    configure_logging(
        environment=os.environ.get("APPSELL_API_ENVIRONMENT", "local"),
        log_level=os.environ.get("APPSELL_API_LOG_LEVEL", "INFO"),
    )


class _SaidaPadraoCorrente:
    """Escreve no `sys.stdout` **do momento da escrita**.

    Guardar `sys.stdout` na configuração congela o destino: quem trocar a saída
    depois — um teste com `capsys`, um script que redireciona — deixa de ver o
    log, e o log parece ter sumido sem nada ter quebrado.
    """

    def write(self, texto: str) -> int:
        return sys.stdout.write(texto)

    def flush(self) -> None:
        sys.stdout.flush()


def _saida_padrao_corrente() -> TextIO:
    # O `WriteLogger` do structlog so chama `write` e `flush`. O `cast` declara
    # esse contrato estreito em vez de fingir um `TextIO` inteiro.
    return cast(TextIO, _SaidaPadraoCorrente())


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)


def request_id() -> str | None:
    """Identificador da requisição em curso, quando existe uma."""
    valor = structlog.contextvars.get_contextvars().get("request_id")
    return valor if isinstance(valor, str) else None


def install_request_logging(app: FastAPI) -> None:
    app.add_middleware(RequestLoggingMiddleware)


class RequestLoggingMiddleware:
    """Uma linha por requisição, sem carregar caminho, query nem cabeçalho.

    É um middleware ASGI puro em vez de `BaseHTTPMiddleware` porque o segundo
    materializa a resposta e quebra `StreamingResponse` — e esta API entrega
    PDF e vídeo.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self._logger = get_logger("appsell_api.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # O silencio vale para a linha de rotina, nunca para a excecao: o
        # `/health/ready` e o endpoint que decide se o Swarm mata a task, e um
        # 500 mudo ali e o mesmo defeito do incidente do socket em `ee194bf`.
        rotina_silenciosa = str(scope.get("path", "")).startswith(_PREFIXO_DE_HEALTH)
        correlacao = _identificador_de_correlacao(scope)
        inicio = time.perf_counter()
        status_visto = 0
        resposta_iniciada = False

        async def enviar(message: Message) -> None:
            nonlocal status_visto, resposta_iniciada
            if message["type"] == "http.response.start":
                status_visto = int(message["status"])
                resposta_iniciada = True
                message = _com_identificador_de_correlacao(message, correlacao)
            await send(message)

        tokens = structlog.contextvars.bind_contextvars(request_id=correlacao)
        try:
            try:
                await self._app(scope, receive, enviar)
            except Exception:
                # A excecao e registrada e **re-levantada**. Responder o 500
                # aqui parecia mais limpo e custava tres coisas: o
                # `ServerErrorMiddleware` do Starlette deixava de ver a falha,
                # um handler registrado com `add_exception_handler(Exception,
                # ...)` nunca rodava, e uma falha depois do inicio da resposta
                # deixava o ciclo ASGI sem o `http.response.body` final. O
                # corpo do 500 tambem mudava de texto para JSON, e o BFF le
                # `detail` como codigo de erro — o codigo exposto viraria
                # "internal server error", com espacos, no lugar de
                # `domain_api_error`.
                self._logger.exception(
                    "unhandled_exception",
                    method=str(scope.get("method", "")),
                    route=_template_da_rota(scope),
                    response_started=resposta_iniciada,
                )
                self._registrar_requisicao(scope, rotina_silenciosa, 500, inicio)
                raise
            self._registrar_requisicao(scope, rotina_silenciosa, status_visto, inicio)
        finally:
            structlog.contextvars.reset_contextvars(**tokens)

    def _registrar_requisicao(
        self, scope: Scope, rotina_silenciosa: bool, status: int, inicio: float
    ) -> None:
        if rotina_silenciosa:
            return
        self._logger.info(
            "http_request",
            method=str(scope.get("method", "")),
            route=_template_da_rota(scope),
            status=status,
            duration_ms=round((time.perf_counter() - inicio) * 1000, 2),
        )


def _redigir_segredos(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    return {chave: _redigir(valor) for chave, valor in event_dict.items()}


def _redigir(valor: Any, profundidade: int = 0) -> Any:
    if isinstance(valor, str):
        return _redigir_texto(valor)
    if profundidade >= _PROFUNDIDADE_MAXIMA:
        # Registrar um evento nao pode derrubar quem o registrou. Uma estrutura
        # ciclica levava o redator a `RecursionError`, e a excecao escapava da
        # chamada de log para o handler.
        return _PROFUNDO_DEMAIS
    if isinstance(valor, dict):
        return {chave: _redigir(item, profundidade + 1) for chave, item in valor.items()}
    if isinstance(valor, list):
        return [_redigir(item, profundidade + 1) for item in valor]
    if isinstance(valor, tuple):
        return tuple(_redigir(item, profundidade + 1) for item in valor)
    return valor


def _redigir_texto(texto: str) -> str:
    # A ordem importa: a credencial de URL e apagada antes dos padroes por
    # tamanho, para que `<REDACTED>` nao ocupe o lugar de onde a credencial
    # comecava e impeca o casamento do `(?<=://)`.
    texto = _CREDENCIAL_EM_URL.sub(_REDIGIDO, texto)
    texto = _CARTAO_CANDIDATO.sub(_redigir_se_for_cartao, texto)
    texto = _BASE64.sub(_redigir_se_for_base64, texto)
    return _SEGREDO.sub(_REDIGIDO, texto)


def _redigir_se_for_base64(casamento: re.Match[str]) -> str:
    achado = casamento.group(0)
    tem_marca = any(marca in achado for marca in _MARCAS_DE_BASE64)
    return _REDIGIDO if tem_marca else achado


def _redigir_se_for_cartao(casamento: re.Match[str]) -> str:
    digitos = [caractere for caractere in casamento.group(0) if caractere.isdigit()]
    return _REDIGIDO if _passa_por_luhn(digitos) else casamento.group(0)


def _passa_por_luhn(digitos: list[str]) -> bool:
    if not 13 <= len(digitos) <= 19:
        return False
    total = 0
    for posicao, caractere in enumerate(reversed(digitos)):
        valor = int(caractere)
        if posicao % 2 == 1:
            valor *= 2
            if valor > 9:
                valor -= 9
        total += valor
    return total % 10 == 0


def _identificador_de_correlacao(scope: Scope) -> str:
    cabecalhos: Iterable[tuple[bytes, bytes]] = scope.get("headers", [])
    for nome, valor in cabecalhos:
        if nome != b"x-request-id":
            continue
        try:
            candidato = valor.decode("ascii")
        except UnicodeDecodeError:
            break
        if _ID_DE_CORRELACAO.fullmatch(candidato):
            return candidato
        break
    return uuid.uuid4().hex[:_DIGITOS_DO_ID_GERADO]


def _com_identificador_de_correlacao(message: Message, correlacao: str) -> Message:
    originais: Iterable[tuple[bytes, bytes]] = message.get("headers", [])
    cabecalhos = [(nome, valor) for nome, valor in originais if nome.lower() != b"x-request-id"]
    cabecalhos.append((b"x-request-id", correlacao.encode("ascii")))
    return {**message, "headers": cabecalhos}


def _template_da_rota(scope: Scope) -> str:
    caminho = getattr(scope.get("route"), "path", None)
    if isinstance(caminho, str):
        return caminho
    return _ROTA_NAO_CASADA


def _nivel(log_level: str) -> int:
    nivel = logging.getLevelName(log_level.upper())
    return nivel if isinstance(nivel, int) else logging.INFO
