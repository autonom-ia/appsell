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

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import zxingcpp
from PIL import Image

#: Resolução da rasterização. Abaixo disto um QR pequeno deixa de ser lido; bem
#: acima, o custo cresce sem melhorar a leitura.
RENDER_DPI: Final = 150
#: Teto de páginas que aceitamos rasterizar atrás de QR. Um PDF longo custaria
#: minutos para procurar um botão que talvez não exista.
MAX_QR_PAGES: Final = 200
#: Distância vertical máxima, em fração da altura da página, entre o botão e o
#: QR do mesmo cartão.
MAX_VERTICAL_DISTANCE: Final = 0.12
RENDER_TIMEOUT_SECONDS: Final = 30

_HTTPS = re.compile(r"^https://[^\s<>\"']{4,2000}$")


class PdfLinkQrUnavailable(RuntimeError):
    """A leitura não pôde ser feita agora."""


@dataclass(frozen=True)
class QrTarget:
    """Um endereço lido da página, e onde ele está."""

    url: str
    #: Centro vertical do QR, de 0 (topo) a 1 (base) da página.
    center: float


def _binary(name: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        raise PdfLinkQrUnavailable(f"{name} is not available in this image")
    return resolved


def read_page_qr_targets(source: Path, page_number: int) -> list[QrTarget]:
    """Os endereços que os QR codes de uma página carregam."""
    if not 1 <= page_number <= MAX_QR_PAGES:
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
            raise PdfLinkQrUnavailable("page rendering exceeded its budget") from error
        except OSError as error:
            raise PdfLinkQrUnavailable("page rendering could not start") from error
        if completed.returncode != 0:
            return []
        rendered = sorted(Path(workspace).glob("page*.png"))
        if not rendered:
            return []
        return _decode(rendered[0])


def _decode(image_path: Path) -> list[QrTarget]:
    with Image.open(image_path) as image:
        altura = image.height
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
        centro = _vertical_center(posicao, altura)
        if centro is None:
            continue
        alvos.append(QrTarget(url=texto, center=centro))
    return alvos


def _vertical_center(posicao: object, altura: int) -> float | None:
    if posicao is None or altura < 1:
        return None
    ys = [
        float(getattr(getattr(posicao, canto, None), "y", float("nan")))
        for canto in ("top_left", "top_right", "bottom_right", "bottom_left")
    ]
    validos = [y for y in ys if y == y]
    if not validos:
        return None
    return min(max(sum(validos) / len(validos) / altura, 0.0), 1.0)


def target_for_rectangle(alvos: list[QrTarget], rect_center: float) -> str | None:
    """O endereço do QR do mesmo cartão que este botão.

    O critério é a proximidade vertical: botão e QR são desenhados lado a lado,
    e cartões diferentes ocupam faixas diferentes da página. Sem um QR perto o
    bastante, o botão continua sem destino — inventar um mandaria o aluno para o
    vídeo errado, que é pior do que não abrir.
    """
    if not alvos:
        return None
    escolhido = min(alvos, key=lambda alvo: abs(alvo.center - rect_center))
    if abs(escolhido.center - rect_center) > MAX_VERTICAL_DISTANCE:
        return None
    return escolhido.url
