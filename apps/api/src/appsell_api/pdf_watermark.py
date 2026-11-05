"""Cópia marcada do PDF baixado (#559).

Roda dentro do sandbox de PDF: o arquivo do vendedor passa primeiro pela mesma
verificação e normalização da leitura on-line (`verified_pdf_file`), e só a
versão normalizada recebe a linha de licença no rodapé de cada página.

A linha é texto de verdade, com fonte padrão do PDF (Helvetica, WinAnsi): sai na
extração de texto e na impressão, e não depende de fonte embutida. O texto entra
no content stream por `pikepdf.unparse_content_stream`, que escapa os
delimitadores do PDF; nada do rótulo vira operador.

A marca é dissuasão, não DRM: quem editar o PDF consegue tirar a linha. O que
ela garante é que a cópia circula com o código da compra e sem o e-mail inteiro.
"""

from __future__ import annotations

import hashlib
import io
import math
from dataclasses import dataclass
from typing import BinaryIO

import pikepdf

from .pdf_raster import PdfRasterRejected, normalize_watermark
from .pdf_verifier import MAX_PDF_BYTES, PDF_MIME, PdfRejected, verified_pdf_file

# Teto de páginas do carimbo. O carimbo e o `save` rodam no processo do
# sandbox, fora dos rlimits do normalizador, e seguram o slot único de mídia: o
# teto limita esse tempo. Material de apoio com mais páginas é recusado.
MAX_STAMP_PAGES = 2_000
# A cópia marcada é o original normalizado mais, por página, um Form XObject e
# dois content streams curtos (cerca de 0,5 KiB). 2 MiB cobrem o teto de
# páginas com folga.
MAX_STAMPED_BYTES = MAX_PDF_BYTES + 2 * 1024 * 1024

_FONT_SIZE = 7.0
_MIN_FONT_SIZE = 3.5
_MARGIN = 18.0
_BASELINE = 10.0
# Largura média de um caractere da Helvetica, em em. Só serve para centralizar e
# para reduzir a fonte em página estreita; o texto não depende dela para sair.
_AVERAGE_EM = 0.5
_SAFE_EM = 0.6
_GRAY = (0.39, 0.45, 0.55)
_HEX_DIGITS = frozenset("0123456789abcdef")


_FormKey = tuple[tuple[tuple[int, int], ...], bytes, tuple[float, float, float, float]]


class PdfWatermarkRejected(ValueError):
    """Entrada fora do contrato: o arquivo ou o rótulo não podem ser marcados."""


@dataclass(frozen=True)
class StampedPdf:
    payload: bytes
    byte_size: int
    sha256_hex: str
    source_sha256_hex: str
    page_count: int


def stamp_pdf(
    source: BinaryIO,
    *,
    expected_size: int,
    expected_sha256_hex: str,
    watermark: str,
    temporary_directory: str | None = None,
) -> StampedPdf:
    """Devolve a cópia do PDF com a linha de licença em todas as páginas."""
    label = _encodable_label(watermark)
    if len(expected_sha256_hex) != 64 or not set(expected_sha256_hex) <= _HEX_DIGITS:
        raise PdfWatermarkRejected("expected SHA-256 is invalid")
    try:
        with verified_pdf_file(
            source,
            expected_size=expected_size,
            expected_sha256_hex=expected_sha256_hex,
            declared_content_type=PDF_MIME,
            temporary_directory=temporary_directory,
            isolated_service_boundary=True,
        ) as verified:
            if verified.page_count > MAX_STAMP_PAGES:
                raise PdfWatermarkRejected("PDF has too many pages to stamp")
            with pikepdf.open(verified.ready_path) as pdf:
                _stamp_pages(pdf, label)
                output = io.BytesIO()
                pdf.save(output, compress_streams=True, deterministic_id=True)
            source_sha256_hex = verified.source_sha256_hex
            page_count = verified.page_count
    except PdfRejected as error:
        raise PdfWatermarkRejected("PDF verification failed") from error
    except pikepdf.PdfError as error:
        raise PdfWatermarkRejected("PDF could not be stamped") from error
    payload = output.getvalue()
    if len(payload) > MAX_STAMPED_BYTES:
        raise PdfWatermarkRejected("stamped PDF is too large")
    return StampedPdf(
        payload=payload,
        byte_size=len(payload),
        sha256_hex=hashlib.sha256(payload).hexdigest(),
        source_sha256_hex=source_sha256_hex,
        page_count=page_count,
    )


def _encodable_label(watermark: str) -> bytes:
    try:
        label = normalize_watermark(watermark)
    except PdfRasterRejected as error:
        raise PdfWatermarkRejected(str(error)) from error
    try:
        # WinAnsi é o conjunto da Helvetica padrão. Caractere fora dele seria
        # trocado por outro na tela, e um rótulo trocado não serve de prova.
        return label.encode("cp1252")
    except UnicodeEncodeError as error:
        raise PdfWatermarkRejected("watermark is outside the font charset") from error


def _stamp_pages(pdf: pikepdf.Pdf, label: bytes) -> None:
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica,
            Encoding=pikepdf.Name.WinAnsiEncoding,
        )
    )
    # Páginas que compartilham o mesmo content stream e os mesmos recursos
    # ganham o mesmo form: o arquivo não cresce uma cópia por página.
    forms: dict[_FormKey, pikepdf.Stream] = {}
    for page in pdf.pages:
        geometry = _page_geometry(page)
        original = _original_as_form(pdf, page, geometry, forms)
        font_name = page.add_resource(font, pikepdf.Name.Font, prefix="AppsellLicense")
        streams: list[pikepdf.Object] = []
        if original is not None:
            # `Do` de um Form XObject salva e restaura o estado gráfico inteiro:
            # um `q` sem `Q`, um `cm` ou um modo de texto que o original deixe
            # pendurado não alcança a linha de licença.
            form_name = page.add_resource(original, pikepdf.Name.XObject, prefix="AppsellPage")
            streams.append(_content(pdf, [([], "q"), ([form_name], "Do"), ([], "Q")]))
        streams.append(_stamp_stream(pdf, geometry, font_name, label))
        page.obj[pikepdf.Name.Contents] = pikepdf.Array(streams)


def _inherited(page: pikepdf.Page, key: pikepdf.Name) -> pikepdf.Object | None:
    """Atributo herdável da página (`/Resources`, `/Rotate`), subindo a árvore."""
    node: pikepdf.Object | None = page.obj
    for _ in range(64):
        if node is None:
            return None
        if key in node:
            return node[key]
        node = node.get(pikepdf.Name.Parent)
    return None


def _original_as_form(
    pdf: pikepdf.Pdf,
    page: pikepdf.Page,
    geometry: _Geometry,
    forms: dict[_FormKey, pikepdf.Stream],
) -> pikepdf.Stream | None:
    contents = page.obj.get(pikepdf.Name.Contents)
    if contents is None:
        return None
    parts = list(contents) if isinstance(contents, pikepdf.Array) else [contents]
    resources = _inherited(page, pikepdf.Name.Resources)
    if not isinstance(resources, pikepdf.Dictionary):
        resources = pikepdf.Dictionary()
    # Chave do reaproveitamento: os streams, os recursos e a caixa. O
    # normalizador deixa `/Resources` direto em cada página, então os recursos
    # entram pela serialização (referências viram "N 0 R"), não pela identidade.
    # Content stream direto não tem identidade, e então não há reaproveitamento.
    stream_ids = tuple(part.objgen for part in parts)
    key = (stream_ids, resources.unparse(), geometry.media)
    shareable = all(objgen != (0, 0) for objgen in stream_ids)
    # A página passa a ter recursos próprios (o carimbo acrescenta a fonte e o
    # form), e o form fica com uma cópia rasa dos recursos de antes.
    page.obj[pikepdf.Name.Resources] = pikepdf.Dictionary(dict(resources.items()))
    if shareable and key in forms:
        return forms[key]
    form = pikepdf.Stream(pdf, b"\n".join(part.read_bytes() for part in parts))
    form[pikepdf.Name.Type] = pikepdf.Name.XObject
    form[pikepdf.Name.Subtype] = pikepdf.Name.Form
    form[pikepdf.Name.BBox] = pikepdf.Array(list(geometry.media))
    form[pikepdf.Name.Resources] = pikepdf.Dictionary(dict(resources.items()))
    indirect = pdf.make_indirect(form)
    if shareable:
        forms[key] = indirect
    return indirect


@dataclass(frozen=True)
class _Geometry:
    left: float
    bottom: float
    right: float
    top: float
    rotation: int
    media: tuple[float, float, float, float]


def _page_geometry(page: pikepdf.Page) -> _Geometry:
    """CropBox e /Rotate validados: valor fora do tipo é recusa (422), não 500."""
    try:
        left, bottom, right, top = (float(value) for value in page.cropbox)
        media_left, media_bottom, media_right, media_top = (float(value) for value in page.mediabox)
        rotate = _inherited(page, pikepdf.Name.Rotate)
        rotation = 0 if rotate is None else int(rotate) % 360
    except (TypeError, ValueError, pikepdf.PdfError) as error:
        raise PdfWatermarkRejected("page geometry is invalid") from error
    media = (media_left, media_bottom, media_right, media_top)
    if not all(map(math.isfinite, (left, bottom, right, top, *media))):
        raise PdfWatermarkRejected("page geometry is invalid")
    if right <= left or top <= bottom or media_right <= media_left or media_top <= media_bottom:
        raise PdfWatermarkRejected("page geometry is invalid")
    if rotation not in (0, 90, 180, 270):
        rotation = 0
    return _Geometry(left, bottom, right, top, rotation, media)


def _content(
    pdf: pikepdf.Pdf, steps: list[tuple[list[pikepdf.Object | float], str]]
) -> pikepdf.Stream:
    instructions = [
        pikepdf.ContentStreamInstruction(operands, pikepdf.Operator(operator))
        for operands, operator in steps
    ]
    return pikepdf.Stream(pdf, pikepdf.unparse_content_stream(instructions))


def _stamp_stream(
    pdf: pikepdf.Pdf, geometry: _Geometry, font_name: pikepdf.Name, label: bytes
) -> pikepdf.Stream:
    width = geometry.right - geometry.left
    height = geometry.top - geometry.bottom
    shown_width = height if geometry.rotation in (90, 270) else width
    matrix = _shown_to_user(
        geometry.rotation, geometry.left, geometry.bottom, geometry.right, geometry.top
    )

    size = min(_FONT_SIZE, (shown_width - 2 * _MARGIN) / max(1, len(label) * _SAFE_EM))
    size = max(_MIN_FONT_SIZE, size)
    x = max(_MARGIN / 2, (shown_width - len(label) * size * _AVERAGE_EM) / 2)
    return _content(
        pdf,
        [
            ([], "q"),
            (list(matrix), "cm"),
            (list(_GRAY), "rg"),
            ([], "BT"),
            ([font_name, size], "Tf"),
            ([x, _BASELINE], "Td"),
            ([pikepdf.String(label)], "Tj"),
            ([], "ET"),
            ([], "Q"),
        ],
    )


def _shown_to_user(
    rotation: int, left: float, bottom: float, right: float, top: float
) -> tuple[float, float, float, float, float, float]:
    """Matriz do espaço de quem lê (origem embaixo à esquerda) para o da página.

    `/Rotate` gira a página no sentido horário na hora de mostrar; a linha tem
    de ficar na borda de baixo do que a pessoa vê, então a matriz desfaz o giro.
    """
    if rotation == 90:
        return (0.0, 1.0, -1.0, 0.0, right, bottom)
    if rotation == 180:
        return (-1.0, 0.0, 0.0, -1.0, right, top)
    if rotation == 270:
        return (0.0, -1.0, 1.0, 0.0, left, top)
    return (1.0, 0.0, 0.0, 1.0, left, bottom)
