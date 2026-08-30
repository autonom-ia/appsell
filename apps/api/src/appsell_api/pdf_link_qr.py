"""O destino de um botão do PDF, lido do QR code ao lado dele.

## Por que isto existe

Um PDF pode trazer o botão desenhado, o QR code impresso, e **nenhum endereço
gravado na anotação de link**. Medido no playbook do vendedor em 30/08/2026: 41
anotações `/Link`, e fora do índice todas com apenas `/BS`, `/NM`, `/Rect` e
`/Subtype` — retângulos de clique sem destino. Uma busca por qualquer endereço
no arquivo inteiro devolveu **zero**.

Quem lê no papel escaneia o QR e chega ao vídeo. Quem lê na tela clica no botão
e não acontece nada. O aluno não tem como saber que a diferença é um campo que
o gerador do PDF esqueceu de escrever.

O endereço existe: está desenhado no QR, em pixels. Este módulo o lê de volta.

## Como

A página é rasterizada e os QR codes são decodificados com posição. Cada
anotação sem destino recebe o endereço do QR **mais próximo na vertical** —
que é o do seu próprio cartão, porque botão e QR são desenhados lado a lado.

O que sai daqui é tratado como qualquer outro link do PDF: só `https`, e a
mesma allowlist de origens decide se o servidor busca alguma coisa.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import zxingcpp
from PIL import Image

from .pdf_link_url import safe_plain_https_url as _safe_plain_https_url

#: Resolução da rasterização. Abaixo disto um QR pequeno deixa de ser lido; bem
#: acima, o custo cresce sem melhorar a leitura.
RENDER_DPI: Final = 150
#: Teto de páginas que aceitamos rasterizar atrás de QR.
MAX_QR_PAGES: Final = 200
#: Orçamento do documento inteiro, em segundos.
#:
#: O teto por página não bastava: um PDF com uma anotação sem destino em cada
#: página dispararia uma rasterização por página, em sequência, segurando o
#: único slot do serviço por dezenas de minutos — e o cliente já teria desistido
#: muito antes. O orçamento é do documento, não da página.
DOCUMENT_BUDGET_SECONDS: Final = 25.0
#: Lado maior da imagem rasterizada. Sem isto, uma página de dimensão absurda
#: vira uma imagem absurda antes de qualquer leitura.
MAX_RENDER_PIXELS: Final = 2500
#: Distância vertical máxima, em fração da altura da página, entre o botão e o
#: QR do mesmo cartão.
MAX_VERTICAL_DISTANCE: Final = 0.12
#: Quanto o segundo candidato precisa estar mais longe, na largura, para o
#: primeiro valer. Abaixo disto o layout é ambíguo, e ambiguidade não vira
#: palpite.
AMBIGUITY_MARGIN: Final = 0.05
#: Diferença de altura abaixo da qual dois QR contam como na mesma faixa.
#:
#: Pequeno de propósito: com uma folga larga, um QR mais distante na vertical
#: poderia ganhar por estar mais perto na horizontal, e a altura deixaria de ser
#: o que decide.
VERTICAL_EPSILON: Final = 0.01
RENDER_TIMEOUT_SECONDS: Final = 30

_HTTPS = re.compile(r"^https://[^\s<>\"']{4,2000}$")


class PdfLinkQrUnavailable(RuntimeError):
    """A leitura não pôde ser feita agora."""


@dataclass(frozen=True)
class QrTarget:
    """Um endereço lido da página, e onde ele está."""

    url: str
    #: Centro do QR, de 0 a 1, em cada eixo da página.
    center: float
    center_x: float


def _binary(name: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        raise PdfLinkQrUnavailable(f"{name} is not available in this image")
    return resolved


class QrBudget:
    """O que resta do orçamento do documento."""

    def __init__(self, seconds: float = DOCUMENT_BUDGET_SECONDS) -> None:
        self._deadline = time.monotonic() + seconds

    def remaining(self) -> float:
        return self._deadline - time.monotonic()


def read_page_qr_targets(
    source: Path, page_number: int, budget: QrBudget | None = None
) -> list[QrTarget]:
    """Os endereços que os QR codes de uma página carregam."""
    if not 1 <= page_number <= MAX_QR_PAGES:
        return []
    restante = budget.remaining() if budget is not None else RENDER_TIMEOUT_SECONDS
    if restante <= 0:
        return []
    with tempfile.TemporaryDirectory(prefix="appsell-qr-") as workspace:
        prefix = Path(workspace) / "page"
        _render(source, page_number, prefix, min(RENDER_TIMEOUT_SECONDS, restante))
        rendered = sorted(Path(workspace).glob("page*.png"))
        if not rendered:
            return []
        if budget is not None and budget.remaining() <= 0:
            # A leitura em si é rápida, mas não começa fora do orçamento: o que
            # sobra do prazo pertence às páginas que ainda faltam.
            return []
        return _decode(rendered[0])


def render_arguments(source: Path, page_number: int, prefix: Path) -> list[str]:
    """O comando da rasterização, separado para poder ser conferido em teste."""
    return [
        _binary("pdftoppm"),
        "-png",
        "-r",
        str(RENDER_DPI),
        # A caixa cujas coordenadas a anotação usa. Sem isto, um `CropBox`
        # diferente do `MediaBox` desloca tudo e o botão casa com o QR errado.
        "-cropbox",
        # Teto do lado maior: sem ele, uma página de dimensão absurda vira uma
        # imagem absurda antes de qualquer leitura.
        "-scale-to",
        str(MAX_RENDER_PIXELS),
        "-f",
        str(page_number),
        "-l",
        str(page_number),
        str(source),
        str(prefix),
    ]


def _render(source: Path, page_number: int, prefix: Path, timeout: float) -> None:
    """Rasteriza uma página, e no estouro mata o grupo inteiro.

    `subprocess.run` mata só o filho direto. O `pdftoppm` pode ter deixado
    descendentes, e eles seguiriam consumindo a máquina depois de já ninguém
    estar esperando pelo resultado.
    """
    try:
        processo = subprocess.Popen(  # noqa: S603 - argv only; local path, no shell
            render_arguments(source, page_number, prefix),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        raise PdfLinkQrUnavailable("page rendering could not start") from error
    try:
        processo.wait(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        _kill_group(processo)
        raise PdfLinkQrUnavailable("page rendering exceeded its budget") from error


def _kill_group(processo: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(os.getpgid(processo.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        processo.kill()
    finally:
        try:
            processo.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return


def _decode(image_path: Path) -> list[QrTarget]:
    with Image.open(image_path) as image:
        altura = image.height
        largura = image.width
        try:
            resultados = zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.QRCode)
        except Exception as error:  # noqa: BLE001 - a biblioteca não declara os seus
            raise PdfLinkQrUnavailable("QR decoding failed") from error
    alvos: list[QrTarget] = []
    for resultado in resultados:
        texto = str(getattr(resultado, "text", "") or "").strip()
        if not _HTTPS.fullmatch(texto):
            # Só endereço seguro vira link. Um QR com texto, telefone ou `http`
            # não tem o que fazer no leitor do aluno.
            continue
        posicao = getattr(resultado, "position", None)
        centro = _center(posicao, altura, "y")
        centro_x = _center(posicao, largura, "x")
        if centro is None or centro_x is None:
            continue
        # O mesmo validador que o manifesto usa. O texto do QR é dado de
        # terceiro: aceitar aqui algo que o manifesto recusa depois invalidaria
        # o manifesto inteiro, e o PDF perderia todos os links por causa de um.
        endereco = _safe_plain_https_url(texto)
        if endereco is None:
            continue
        alvos.append(QrTarget(url=endereco, center=centro, center_x=centro_x))
    return alvos


def _center(posicao: object, tamanho: int, eixo: str) -> float | None:
    if posicao is None or tamanho < 1:
        return None
    valores = [
        float(getattr(getattr(posicao, canto, None), eixo, float("nan")))
        for canto in ("top_left", "top_right", "bottom_right", "bottom_left")
    ]
    validos = [v for v in valores if v == v]
    if not validos:
        return None
    return min(max(sum(validos) / len(validos) / tamanho, 0.0), 1.0)


def assign_targets(
    alvos: list[QrTarget], retangulos: list[tuple[float, float]]
) -> list[str | None]:
    """Distribui os QR entre os botões da página, um para cada.

    Um QR pertence a um cartão. Deixar dois botões receberem o mesmo endereço
    manda um dos dois alunos para o vídeo errado — e errado em silêncio, que é
    pior do que o botão não abrir.

    A altura decide, porque o cartão ocupa uma faixa da página e o QR está na
    mesma faixa do botão. A largura só desempata quando as alturas empatam, que
    é o caso de layout em colunas: somar os dois eixos não funciona, porque a
    distância horizontal entre botão e QR é a mesma para todos os candidatos e
    abafaria a diferença vertical que de fato distingue os cartões.
    """
    disponiveis = list(alvos)
    resultado: list[str | None] = []
    for centro_y, centro_x in retangulos:
        escolhido = _pick(disponiveis, centro_y, centro_x)
        resultado.append(escolhido.url if escolhido else None)
        if escolhido is not None:
            disponiveis.remove(escolhido)
    return resultado


def _pick(alvos: list[QrTarget], centro_y: float, centro_x: float) -> QrTarget | None:
    perto = [alvo for alvo in alvos if abs(alvo.center - centro_y) <= MAX_VERTICAL_DISTANCE]
    if not perto:
        return None
    por_altura = sorted(perto, key=lambda alvo: abs(alvo.center - centro_y))
    if len(por_altura) == 1:
        return por_altura[0]

    primeiro = abs(por_altura[0].center - centro_y)
    if abs(por_altura[1].center - centro_y) - primeiro >= VERTICAL_EPSILON:
        return por_altura[0]

    # Mesma faixa vertical: layout em colunas, e aí a largura decide — com folga.
    empatados = [
        alvo
        for alvo in por_altura
        if abs(abs(alvo.center - centro_y) - primeiro) < VERTICAL_EPSILON
    ]
    por_lado = sorted(empatados, key=lambda alvo: abs(alvo.center_x - centro_x))
    folga = abs(por_lado[1].center_x - centro_x) - abs(por_lado[0].center_x - centro_x)
    return por_lado[0] if folga >= AMBIGUITY_MARGIN else None
