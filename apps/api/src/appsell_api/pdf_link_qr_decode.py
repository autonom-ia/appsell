"""A leitura em si: rasteriza a página e decodifica os QR.

Roda dentro de `pdf_link_qr_worker`, num processo que o pai pode matar.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import zxingcpp
from PIL import Image

from .pdf_link_url import safe_plain_https_url

#: Resolução da rasterização.
#:
#: Medido no servidor com o PDF do vendedor: a 150 dpi cada página custa 0,91 s
#: e a 100 dpi custa 0,39 s, **com a mesma leitura** — o QR é grande e não
#: precisa de tanto detalhe. A 75 dpi ele deixa de ser lido, então este é o
#: ponto: o mais barato que ainda enxerga.
RENDER_DPI: Final = 100
#: Lado maior da imagem. Sem isto, uma página de dimensão absurda vira uma
#: imagem absurda antes de qualquer leitura.
MAX_RENDER_PIXELS: Final = 1600


@dataclass(frozen=True)
class QrTarget:
    """Um endereço lido da página, e onde ele está."""

    url: str
    #: Centro do QR, de 0 a 1, em cada eixo da página.
    center: float
    center_x: float


def render_arguments(source: Path, page_number: int, prefix: Path) -> list[str]:
    """O comando da rasterização, separado para poder ser conferido em teste."""
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        raise RuntimeError("pdftoppm is not available in this image")
    return [
        pdftoppm,
        "-png",
        "-r",
        str(RENDER_DPI),
        # A caixa cujas coordenadas a anotação usa. Sem isto, um `CropBox`
        # diferente do `MediaBox` desloca tudo e o botão casa com o QR errado.
        "-cropbox",
        "-scale-to",
        str(MAX_RENDER_PIXELS),
        "-f",
        str(page_number),
        "-l",
        str(page_number),
        str(source),
        str(prefix),
    ]


def decode_page(source: Path, page_number: int) -> list[QrTarget]:
    with tempfile.TemporaryDirectory(prefix="appsell-qr-") as workspace:
        prefix = Path(workspace) / "page"
        completed = subprocess.run(  # noqa: S603 - argv only; local path, no shell
            render_arguments(source, page_number, prefix),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if completed.returncode != 0:
            return []
        rendered = sorted(Path(workspace).glob("page*.png"))
        return decode_image(rendered[0]) if rendered else []


def decode_image(image_path: Path) -> list[QrTarget]:
    with Image.open(image_path) as image:
        altura, largura = image.height, image.width
        resultados = zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.QRCode)
    alvos: list[QrTarget] = []
    for resultado in resultados:
        texto = str(getattr(resultado, "text", "") or "").strip()
        posicao = getattr(resultado, "position", None)
        centro = center(posicao, altura, "y")
        centro_x = center(posicao, largura, "x")
        if centro is None or centro_x is None:
            continue
        # O mesmo validador que o manifesto usa. Aceitar aqui algo que ele
        # recusa depois invalidaria o manifesto inteiro, e o PDF perderia todos
        # os links por causa de um.
        endereco = safe_plain_https_url(texto)
        if endereco is None:
            continue
        alvos.append(QrTarget(url=endereco, center=centro, center_x=centro_x))
    return alvos


def center(posicao: object, tamanho: int, eixo: str) -> float | None:
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
