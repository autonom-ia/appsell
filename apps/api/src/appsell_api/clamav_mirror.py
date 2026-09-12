"""A versão da base de assinaturas que o mirror do ClamAV anuncia.

Existe para responder a uma pergunta que a idade da assinatura não responde:
estamos com a base mais nova que existe, ou paramos de conseguir atualizar? Foi
a confusão entre as duas que tirou `sell.autonomia.site` do ar por 3h06 em
12/09/2026 — uma base de 50h foi reprovada por um teto de 48h quando era, ela
mesma, a mais recente publicada no mundo.

A fonte é a que o próprio `freshclam` consulta, um TXT no DNS:

    current.cvd.clamav.net.  IN  TXT  "1.4.6:63:28121:1789203600:1:90:49192:339"
                                              ^^^^^ base diária

A consulta é montada à mão em UDP porque a imagem não traz `dig`, `nslookup`
nem `dnspython`, e nenhuma das três justifica virar dependência para ler um
número. Nada aqui levanta exceção: qualquer falha vira `None`, e quem chama
trata isso como "não sei", nunca como "estou desatualizado".
"""

from __future__ import annotations

import random
import socket
import struct
import threading
from pathlib import Path
from time import monotonic

_MIRROR = "current.cvd.clamav.net"
_RESOLV_CONF = Path("/etc/resolv.conf")
_PORTA_DNS = 53
_TIPO_TXT = 16
_CLASSE_IN = 1
# Resposta de DNS em UDP não passa disto sem truncar, e o que se lê aqui vem da
# rede: o limite é o que impede uma resposta absurda de virar trabalho nosso.
_TAMANHO_MAXIMO = 4096
# Um TXT com mais campos que isto não é o formato que se espera ler.
_SALTOS_MAXIMOS = 16


# A base publicada muda cerca de uma vez por dia, e `healthy()` é perguntado
# antes de cada arquivo da fila e a cada passagem do monitor. Sem reaproveitar,
# cada uma dessas perguntas vira uma consulta — e, com o DNS calado, cada uma
# espera o tempo limite antes de responder.
_VALIDADE_DA_RESPOSTA = 300.0
# Falha fica valendo bem menos: é estado que se quer reavaliar logo, não um
# veredito para carregar por cinco minutos.
_VALIDADE_DA_FALHA = 30.0

_cache_lock = threading.Lock()
_cache: tuple[float, int | None] | None = None


def limpa_cache_do_mirror() -> None:
    """Descarta a resposta guardada. Existe para os testes não vazarem estado."""
    global _cache
    with _cache_lock:
        _cache = None


def daily_publicada_no_mirror(*, timeout_seconds: float = 3.0) -> int | None:
    """A base diária anunciada pelo mirror, ou `None` se não der para saber."""
    global _cache
    agora = monotonic()
    with _cache_lock:
        if _cache is not None:
            guardado_em, valor = _cache
            validade = _VALIDADE_DA_RESPOSTA if valor is not None else _VALIDADE_DA_FALHA
            if agora - guardado_em <= validade:
                return valor

    valor = _consulta(timeout_seconds=timeout_seconds)
    with _cache_lock:
        _cache = (monotonic(), valor)
    return valor


def _consulta(*, timeout_seconds: float) -> int | None:
    for servidor in _nameservers():
        texto = _consulta_txt(servidor, timeout_seconds=timeout_seconds)
        if texto is None:
            continue
        daily = _extrai_daily(texto)
        if daily is not None:
            return daily
    return None


def _nameservers() -> list[str]:
    """Os resolvedores do próprio contêiner, na ordem em que ele os usa."""
    try:
        linhas = _RESOLV_CONF.read_text(encoding="ascii", errors="replace").splitlines()
    except OSError:
        return []
    servidores = []
    for linha in linhas:
        partes = linha.split()
        if len(partes) >= 2 and partes[0] == "nameserver":
            servidores.append(partes[1])
    return servidores[:3]


def _consulta_txt(servidor: str, *, timeout_seconds: float) -> str | None:
    pedido_id = random.SystemRandom().randrange(0, 0xFFFF)  # noqa: S311 - não é sorteio secreto
    pergunta = _monta_pergunta(pedido_id)
    # Um resolvedor IPv6 com família IPv4 falha de saída. O `resolv.conf` do
    # contêiner hoje traz o DNS interno do Docker, que é IPv4, mas a máquina de
    # quem desenvolve costuma listar um link-local IPv6 primeiro.
    familia = socket.AF_INET6 if ":" in servidor else socket.AF_INET
    try:
        with socket.socket(familia, socket.SOCK_DGRAM) as cliente:
            cliente.settimeout(timeout_seconds)
            cliente.sendto(pergunta, (servidor, _PORTA_DNS))
            resposta, _ = cliente.recvfrom(_TAMANHO_MAXIMO)
    except (OSError, ValueError):
        return None
    return _le_resposta(resposta, pedido_id=pedido_id)


def _monta_pergunta(pedido_id: int) -> bytes:
    cabecalho = struct.pack("!HHHHHH", pedido_id, 0x0100, 1, 0, 0, 0)
    nome = b"".join(bytes([len(parte)]) + parte for parte in _MIRROR.encode("ascii").split(b"."))
    return cabecalho + nome + b"\x00" + struct.pack("!HH", _TIPO_TXT, _CLASSE_IN)


def _le_resposta(resposta: bytes, *, pedido_id: int) -> str | None:
    if len(resposta) < 12:
        return None
    eco_id, _flags, perguntas, respostas = struct.unpack("!HHHH", resposta[:8])
    if eco_id != pedido_id or respostas < 1:
        return None

    posicao = 12
    for _ in range(perguntas):
        depois_do_nome = _pula_nome(resposta, posicao)
        if depois_do_nome is None:
            return None
        posicao = depois_do_nome + 4  # tipo e classe da pergunta
    for _ in range(respostas):
        depois_do_nome = _pula_nome(resposta, posicao)
        if depois_do_nome is None or depois_do_nome + 10 > len(resposta):
            return None
        posicao = depois_do_nome
        tipo, _classe, _ttl, tamanho = struct.unpack("!HHIH", resposta[posicao : posicao + 10])
        posicao += 10
        if posicao + tamanho > len(resposta):
            return None
        if tipo == _TIPO_TXT:
            return _le_texto(resposta[posicao : posicao + tamanho])
        posicao += tamanho
    return None


def _pula_nome(dados: bytes, posicao: int) -> int | None:
    """Anda por cima de um nome, inclusive quando ele é um ponteiro."""
    for _ in range(_SALTOS_MAXIMOS):
        if posicao >= len(dados):
            return None
        tamanho = dados[posicao]
        if tamanho == 0:
            return posicao + 1
        if tamanho & 0xC0 == 0xC0:
            # Ponteiro de compressão: dois bytes e o nome acabou aqui.
            return posicao + 2
        posicao += 1 + tamanho
    return None


def _le_texto(rdata: bytes) -> str | None:
    """O RDATA de um TXT é uma sequência de pedaços com o tamanho na frente."""
    pedacos = []
    posicao = 0
    for _ in range(_SALTOS_MAXIMOS):
        if posicao >= len(rdata):
            break
        tamanho = rdata[posicao]
        posicao += 1
        pedacos.append(rdata[posicao : posicao + tamanho])
        posicao += tamanho
    if not pedacos:
        return None
    try:
        return b"".join(pedacos).decode("ascii")
    except UnicodeDecodeError:
        return None


def _extrai_daily(texto: str) -> int | None:
    """O terceiro campo do TXT é a base diária."""
    campos = texto.split(":")
    if len(campos) < 3:
        return None
    try:
        daily = int(campos[2])
    except ValueError:
        return None
    return daily if daily > 0 else None
