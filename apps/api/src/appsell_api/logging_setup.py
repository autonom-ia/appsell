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
import threading
import time
import traceback
import uuid
from collections.abc import Iterable, Mapping, MutableMapping
from types import TracebackType
from typing import Any, TextIO, cast

import structlog
from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_InformacaoDeExcecao = tuple[type[BaseException], BaseException, TracebackType | None]

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

# Nem tudo que e comprido e segredo. UUID e slug sao **identificadores**, e este
# dominio identifica asset, entitlement e publicacao por UUID e produto e seller
# por slug. Redigi-los apagava exatamente o `asset_id` que o proprio
# TROUBLESHOOTING manda registrar, e o `curso-de-...-na-pratica-2026` que diz de
# qual produto o erro fala.
#
# Dois criterios, e os dois sao necessarios. O **tamanho do maior trecho sem
# separador** — um token carrega uma corrida longa, um slug e feito de palavras
# curtas, um UUID de grupos de no maximo 12. E a **ausencia de maiuscula**: o
# `_SLUG` de `routes.py` so aceita minuscula, digito e hifen, e sem este segundo
# criterio 5% dos `secrets.token_urlsafe(32)` que o proprio sistema gera em
# `platform_routes.py` passavam intactos, porque por acaso nao tinham nenhum
# trecho longo. Um token so escaparia agora se sorteasse 43 caracteres sem uma
# unica maiuscula, o que nao acontece na pratica.
_UUID_CANONICO = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
_SEPARADORES_DE_IDENTIFICADOR = re.compile(r"[-_]")
_TRECHO_MAXIMO_DE_IDENTIFICADOR = 16

# 2. Segredo em base64 padrao. `+`, `/` e `=` quebram a corrida do padrao acima
#    em pedacos curtos, e o valor inteiro passava.
#
#    O casamento exige `+` ou `=` — nao basta `/`. Caminho de arquivo e cheio de
#    `/` e aparece em todo traceback: `/Users/alguem/dev/worktrees` tem 27
#    caracteres do alfabeto base64 e viraria `<REDACTED>`, apagando justamente a
#    parte do traceback que diz onde o erro aconteceu. Um segredo base64 que nao
#    tenha nem `+` nem `=` escapa deste padrao; e um limite conhecido, nao um
#    descuido.
#    O `=` so conta como padding **no fim**. Aceitar `=` em qualquer posicao
#    fazia `correlation=abcdefghijklmnop` desaparecer inteiro, e exigir apenas
#    `+` fazia um caminho com diretorio `C++` perder o comeco.
_BASE64 = re.compile(r"[A-Za-z0-9+/]{24,}={1,2}")

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
#    `?` e `#` encerram a autoridade: sem exclui-los,
#    `http://api.internal:8080?notify=ops@autonomia.site` virava
#    `http://<REDACTED>@autonomia.site` — a porta era confundida com usuario e o
#    host desaparecia.
_CREDENCIAL_EM_URL = re.compile(r"(?<=://)[^/\s?#]*:[^/\s?#]*(?=@)")

# 4. Numero de cartao. Redigir por tamanho apagaria identificador de pedido e de
#    asset; o digito verificador de Luhn separa os dois casos sem chute.
_CARTAO_CANDIDATO = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")

# Visa, Amex, Diners e Discover no que tem prefixo fixo.
_PREFIXOS_LITERAIS_DE_BANDEIRA = ("4", "34", "37", "36", "38", "6011", "65")
# (quantos digitos olhar, inicio, fim) — Mastercard, Diners, Discover, JCB e
# UnionPay, que sao definidas por faixa e nao por prefixo literal.
_FAIXAS_DE_BANDEIRA = (
    (2, 51, 55),
    (2, 62, 62),
    (3, 300, 305),
    (3, 644, 649),
    (4, 2221, 2720),
    (4, 3528, 3589),
)

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

# O uvicorn instala tratadores proprios nestes loggers no arranque do servidor.
_LOGGERS_COM_TRATADOR_PROPRIO = ("uvicorn", "uvicorn.error", "uvicorn.access")

# Guardados no import, antes de qualquer substituicao, para que reconfigurar o
# log varias vezes nao encadeie um hook dentro do outro.
_EXCEPTHOOK_ORIGINAL = sys.excepthook
_UNRAISABLEHOOK_ORIGINAL = sys.unraisablehook
_EXCEPTHOOK_DE_THREAD_ORIGINAL = threading.__excepthook__

# Controle de fluxo, nao falha. O hook padrao de thread ignora `SystemExit` em
# silencio e imprime `KeyboardInterrupt`; os dois sao delegados a ele para
# preservar esse comportamento. Transformar `sys.exit(0)` numa thread em
# incidente critico seria alarme falso, e alarme falso e como um log volta a ser
# ignorado.
_CONTROLES_DE_FLUXO = (KeyboardInterrupt, SystemExit)

# Capturar a raiz em INFO traz junto o log de cliente destas bibliotecas. O
# `httpx` registra `HTTP Request: GET <url completa>` a cada chamada — e a API
# fala com o sandbox e com o identity por `httpx`, de modo que a URL inteira,
# com a query, iria para o log a cada requisicao interna. Ruido e risco ao mesmo
# tempo. Em `WARNING` elas so falam quando ha o que dizer.
_LOGGERS_RUIDOSOS = (
    "asyncio",
    "boto3",
    "botocore",
    "httpcore",
    "httpx",
    "s3transfer",
    "urllib3",
)


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
    nivel = _nivel(log_level)
    saida = stream or _saida_de_diagnostico_corrente()
    renderizador: Any = (
        structlog.processors.JSONRenderer()
        if estruturado
        # `colors=False` nao alcanca o formatador de excecao: ele continua
        # colorindo o traceback, e os codigos ANSI no meio do texto quebram os
        # padroes do redator — `alice` saia inteiro entre dois escapes. O
        # `plain_traceback` e o mesmo traceback sem cor nenhuma.
        else structlog.dev.ConsoleRenderer(
            colors=False, exception_formatter=structlog.dev.plain_traceback
        )
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
    # O redator roda duas vezes, e a segunda nao e zelo excessivo. Em
    # `local`/`ci` o `format_exc_info` fica fora da cadeia — o `ConsoleRenderer`
    # formata a excecao sozinho —, entao o redator estrutural so ve
    # `exc_info=True` e o traceback e montado **depois** dele. Sem a passada
    # final, `RuntimeError("postgresql://alice:p@ssw0rd@db.internal/prod")`
    # saia com usuario e senha inteiros, e num run de CI isso fica gravado no
    # log publico do GitHub.
    #
    # As duas passadas sao redundantes de proposito, e a redundancia foi medida:
    # desligar qualquer uma delas isoladamente nao faz nenhum teste de vazamento
    # reprovar. Cada uma cobre a outra.
    processadores += [_redigir_segredos, renderizador, _redigir_a_linha_pronta]
    structlog.configure(
        processors=processadores,
        logger_factory=structlog.WriteLoggerFactory(file=saida),
        wrapper_class=structlog.make_filtering_bound_logger(nivel),
        cache_logger_on_first_use=False,
    )
    _capturar_o_logging_da_biblioteca_padrao(
        saida=saida, nivel=nivel, estruturado=estruturado, renderizador=renderizador
    )
    _capturar_a_excecao_terminal()


def _capturar_o_logging_da_biblioteca_padrao(
    *, saida: TextIO, nivel: int, estruturado: bool, renderizador: Any
) -> None:
    """Faz o `logging` da biblioteca padrão passar pelos mesmos processadores.

    Sem isto o redator é contornado exatamente no caso mais perigoso. Medido
    contra o uvicorn real: uma exceção não tratada saía **duas vezes** — a linha
    estruturada, com a credencial redigida, e logo abaixo o traceback que o
    uvicorn imprime pelo `logging` padrão, com
    `postgresql://alice:p@ss@db.internal/prod` em claro.

    Como efeito colateral desejado, tudo o que uvicorn, SQLAlchemy e boto3
    registram passa a sair no mesmo formato e com a mesma redação.
    """
    pre_cadeia: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if estruturado:
        pre_cadeia.append(structlog.processors.format_exc_info)
    formatador = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_cadeia,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _redigir_segredos,
            renderizador,
            _redigir_a_linha_pronta,
        ],
    )
    tratador = logging.StreamHandler(saida)
    tratador.setFormatter(formatador)

    raiz = logging.getLogger()
    for anterior in list(raiz.handlers):
        raiz.removeHandler(anterior)
    raiz.addHandler(tratador)
    raiz.setLevel(nivel)

    # O uvicorn instala tratadores proprios nestes tres, e um tratador proprio
    # significa uma copia crua ao lado da linha redigida. Esvaziar e deixar
    # propagar para a raiz e o que faz a captura valer.
    for nome in _LOGGERS_COM_TRATADOR_PROPRIO:
        logger_da_biblioteca = logging.getLogger(nome)
        logger_da_biblioteca.handlers = []
        logger_da_biblioteca.propagate = True

    for nome in _LOGGERS_RUIDOSOS:
        logging.getLogger(nome).setLevel(max(nivel, logging.WARNING))


def _capturar_a_excecao_terminal() -> None:
    """Faz a exceção que derruba o processo passar pela cadeia redigida.

    O `sys.excepthook` padrão escreve o traceback direto no `stderr`, sem passar
    por structlog, pelo tratador da raiz nem pelas duas passadas do redator. É o
    caminho de uma falha de arranque — `asset_worker_service.main()` chama
    `asyncio.run(run())` sem rede, e `ensure-asset-worker.sh` roda isso em
    produção. Reproduzido com
    `RuntimeError("postgresql://alice:p@ssw0rd@db.internal/prod")`: usuário e
    senha apareciam inteiros.

    São **três** hooks, e cada um cobre um caminho que os outros não veem:
    `sys.excepthook` para a thread principal, `threading.excepthook` para as
    demais, e `sys.unraisablehook` para o que acontece fora de qualquer pilha —
    `atexit`, `__del__`, finalizador de `weakref`. Esse último processo terminava
    com código 0 e a credencial inteira no `stderr`.

    O interpretador continua saindo com código diferente de zero: os hooks
    apenas registram. `KeyboardInterrupt` e `SystemExit` seguem pelos hooks
    originais — são controle de fluxo, não falha.
    """
    logger = get_logger("appsell_api.terminal")

    def registrar(evento: str, informacao: _InformacaoDeExcecao, **extra: Any) -> None:
        try:
            logger.critical(evento, exc_info=informacao, **extra)
        except Exception:  # noqa: BLE001 - ver `_ultimo_recurso`
            _ultimo_recurso(evento, informacao)

    def na_thread_principal(
        tipo: type[BaseException], valor: BaseException, tb: TracebackType | None
    ) -> None:
        if issubclass(tipo, _CONTROLES_DE_FLUXO):
            _EXCEPTHOOK_ORIGINAL(tipo, valor, tb)
            return
        registrar("uncaught_exception", (tipo, valor, tb))

    def nas_outras_threads(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is None or issubclass(args.exc_type, _CONTROLES_DE_FLUXO):
            _EXCEPTHOOK_DE_THREAD_ORIGINAL(args)
            return
        registrar(
            "uncaught_thread_exception",
            (args.exc_type, args.exc_value, args.exc_traceback),
            thread=getattr(args.thread, "name", None),
        )

    def fora_de_qualquer_pilha(args: Any) -> None:
        tipo = getattr(args, "exc_type", None)
        valor = getattr(args, "exc_value", None)
        if tipo is None or valor is None:
            _UNRAISABLEHOOK_ORIGINAL(args)
            return
        registrar(
            "unraisable_exception",
            (tipo, valor, getattr(args, "exc_traceback", None)),
            context=_redigir_texto(str(getattr(args, "err_msg", "") or "")),
        )

    sys.excepthook = na_thread_principal
    threading.excepthook = nas_outras_threads
    sys.unraisablehook = fora_de_qualquer_pilha


def _ultimo_recurso(evento: str, informacao: _InformacaoDeExcecao) -> None:
    """Escreve a linha mínima quando o próprio canal de registro falhou.

    Sem isto, uma falha dentro do hook — fluxo de saída fechado, renderizador
    quebrado — faz o CPython imprimir `Error in sys.excepthook` seguido da
    exceção **original e crua**, com credencial e tudo. Medido.

    Este é o único ponto do módulo onde uma exceção é engolida, e é onde engolir
    é a única saída: quem falhou foi o canal de registro, então não há para onde
    registrar o motivo, e deixar escapar devolve o problema ao hook padrão do
    CPython — que é exatamente o vazamento a evitar.
    """
    try:
        texto = "".join(traceback.format_exception(*informacao))
        sys.stderr.write(f"{evento} {_redigir_texto(texto)}\n")
    except Exception:  # noqa: BLE001, S110 - nao ha canal restante para registrar isto
        pass


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


class _SaidaDeDiagnosticoCorrente:
    """Escreve no `sys.stderr` **do momento da escrita**.

    Duas decisões aqui, e as duas foram aprendidas quebrando coisa.

    **Por que `stderr`.** O `stdout` é canal de protocolo em pelo menos um
    caminho crítico: `pdf_verifier` roda o parser num subprocesso e faz
    `json.loads(completed.stdout)`, com um teto de 1.024 bytes. Uma única linha
    de log naquele processo quebra a leitura de **todo** PDF — e a captura do
    `logging` da biblioteca padrão torna isso provável, porque basta um
    `WARNING` de qualquer dependência. `pdf_runtime_probe` e
    `capacity_telemetry` também imprimem JSON puro no `stdout`. O Swarm captura
    os dois fluxos, então nada se perde no `docker service logs`.

    **Por que resolver na hora da escrita.** Guardar o fluxo na configuração
    congela o destino: quem trocar a saída depois — um teste com `capsys`, um
    script que redireciona — deixa de ver o log, e o log parece ter sumido sem
    nada ter quebrado.
    """

    def write(self, texto: str) -> int:
        return sys.stderr.write(texto)

    def flush(self) -> None:
        sys.stderr.flush()


def _saida_de_diagnostico_corrente() -> TextIO:
    # O `WriteLogger` do structlog so chama `write` e `flush`. O `cast` declara
    # esse contrato estreito em vez de fingir um `TextIO` inteiro.
    return cast(TextIO, _SaidaDeDiagnosticoCorrente())


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
                # Nao existe `http_request` aqui, e isso e deliberado. Quem
                # decide o status final e o `ServerErrorMiddleware` — ou um
                # handler global, que pode responder 503 — e os dois ficam
                # **fora** deste middleware. Registrar 500 por conta propria
                # fazia a linha mentir em dois casos medidos: resposta em fluxo
                # que ja tinha comecado com 200, e handler global respondendo
                # 503. O evento abaixo carrega tudo o que a linha carregaria.
                self._logger.exception(
                    "unhandled_exception",
                    method=str(scope.get("method", "")),
                    route=_template_da_rota(scope),
                    response_started=resposta_iniciada,
                    status_started=status_visto or None,
                    duration_ms=round((time.perf_counter() - inicio) * 1000, 2),
                )
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


def _redigir_a_linha_pronta(_logger: Any, _method_name: str, linha: Any) -> Any:
    """Última passada, sobre o texto que o renderizador acabou de montar.

    O redator estrutural vê os campos; este vê o que de fato vai sair. É o que
    alcança o traceback formatado pelo `ConsoleRenderer`, que é montado depois
    do outro.
    """
    return _redigir_texto(linha) if isinstance(linha, str) else linha


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
    texto = _BASE64.sub(_REDIGIDO, texto)
    return _SEGREDO.sub(_redigir_se_nao_for_identificador, texto)


def _redigir_se_nao_for_identificador(casamento: re.Match[str]) -> str:
    achado = casamento.group(0)
    return achado if _parece_identificador(achado) else _REDIGIDO


def _parece_identificador(texto: str) -> bool:
    if _UUID_CANONICO.fullmatch(texto):
        return True
    if texto != texto.lower():
        return False
    trechos = _SEPARADORES_DE_IDENTIFICADOR.split(texto)
    return all(len(trecho) <= _TRECHO_MAXIMO_DE_IDENTIFICADOR for trecho in trechos)


def _redigir_se_for_cartao(casamento: re.Match[str]) -> str:
    digitos = [caractere for caractere in casamento.group(0) if caractere.isdigit()]
    if not _comeca_como_cartao(digitos):
        return casamento.group(0)
    return _REDIGIDO if _passa_por_luhn(digitos) else casamento.group(0)


def _comeca_como_cartao(digitos: list[str]) -> bool:
    """Luhn sozinho não separa cartão de identificador.

    Cerca de um em cada dez números arbitrários passa no dígito verificador — o
    carimbo Unix `1788134400000` passa, e `0000000000000` também. Exigir também
    um prefixo de bandeira emissora derruba o falso positivo sem perder cartão
    real.

    A lista precisa cobrir a bandeira inteira, não o exemplo mais conhecido
    dela: `6011` e `65` deixavam de fora os intervalos `644-649` e
    `622126-622925` da Discover, e `6445641386133871` passava por Luhn e saía
    inteiro no log.
    """
    numero = "".join(digitos)
    if numero.startswith(_PREFIXOS_LITERAIS_DE_BANDEIRA):
        return True
    return any(
        numero[:tamanho].isdigit() and inicio <= int(numero[:tamanho]) <= fim
        for tamanho, inicio, fim in _FAIXAS_DE_BANDEIRA
    )


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
