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
#: Quanto o segundo candidato precisa estar mais longe para o primeiro valer.
#: Abaixo disto o layout é ambíguo, e ambiguidade não vira palpite.
AMBIGUITY_MARGIN: Final = 0.05
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
        try:
            completed = subprocess.run(  # noqa: S603 - argv only; local path, no shell
                [
                    _binary("pdftoppm"),
                    "-png",
                    "-r",
                    str(RENDER_DPI),
                    "-f",
                    str(page_number),
                    "-l",
                    str(page_number),
                    str(source),
                    str(prefix),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=RENDER_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            _kill_group(error)
            raise PdfLinkQrUnavailable("page rendering exceeded its budget") from error
        except OSError as error:
            raise PdfLinkQrUnavailable("page rendering could not start") from error
        if completed.returncode != 0:
            return []
        rendered = sorted(Path(workspace).glob("page*.png"))
        if not rendered:
            return []
        return _decode(rendered[0])


def _kill_group(error: subprocess.TimeoutExpired) -> None:
    """Mata o grupo do processo que estourou, e não só ele."""
    pid = getattr(getattr(error, "cmd", None), "pid", None) or 0
    try:
        if pid:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
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


def target_for_rectangle(
    alvos: list[QrTarget], rect_center: float, rect_center_x: float
) -> str | None:
    """O endereço do QR do mesmo cartão que este botão.

    Botão e QR são desenhados lado a lado, então quem decide é a **altura**: o
    cartão ocupa uma faixa da página, e o QR está na mesma faixa que o botão.

    A largura entra só para desempatar. Somá-la à altura não funciona — o botão
    fica à esquerda e o QR à direita, e essa distância horizontal, igual para
    todos os candidatos, abafaria a diferença vertical que de fato distingue os
    cartões.

    Ambiguidade não vira palpite. Se dois QR estão à mesma altura e à mesma
    distância lateral, ninguém ganha: mandar o aluno para o vídeo errado é pior
    do que o botão não abrir.
    """
    perto = [alvo for alvo in alvos if abs(alvo.center - rect_center) <= MAX_VERTICAL_DISTANCE]
    if not perto:
        return None
    por_altura = sorted(perto, key=lambda alvo: abs(alvo.center - rect_center))
    if len(por_altura) == 1:
        return por_altura[0].url

    primeiro = abs(por_altura[0].center - rect_center)
    segundo = abs(por_altura[1].center - rect_center)
    if segundo - primeiro >= AMBIGUITY_MARGIN:
        return por_altura[0].url

    # Mesma faixa vertical: layout em colunas. Aí a largura decide, e precisa
    # decidir com folga.
    empatados = [
        alvo
        for alvo in por_altura
        if abs(abs(alvo.center - rect_center) - primeiro) < AMBIGUITY_MARGIN
    ]
    por_lado = sorted(empatados, key=lambda alvo: abs(alvo.center_x - rect_center_x))
    if len(por_lado) > 1:
        folga = abs(por_lado[1].center_x - rect_center_x) - abs(
            por_lado[0].center_x - rect_center_x
        )
        if folga < AMBIGUITY_MARGIN:
            return None
    return por_lado[0].url


def _distancia(alvo: QrTarget, centro_y: float, centro_x: float) -> float:
    return float(((alvo.center - centro_y) ** 2 + (alvo.center_x - centro_x) ** 2) ** 0.5)
