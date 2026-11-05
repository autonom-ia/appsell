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
from dataclasses import dataclass
from typing import BinaryIO

import pikepdf

from .pdf_raster import PdfRasterRejected, normalize_watermark
from .pdf_verifier import MAX_PDF_BYTES, PDF_MIME, PdfRejected, verified_pdf_file

# A cópia marcada é o original normalizado mais um content stream curto por
# página; uma folga de 1 MiB cobre milhares de páginas.
MAX_STAMPED_BYTES = MAX_PDF_BYTES + 1024 * 1024

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
    for page in pdf.pages:
        font_name = page.add_resource(font, pikepdf.Name.Font, prefix="AppsellLicense")
        # O conteúdo original vai entre q/Q: um `cm` ou cor que ele deixe
        # pendurado não desloca nem pinta a linha.
        page.contents_add(b"q\n", prepend=True)
        page.contents_add(b"\nQ\n")
        page.contents_add(_stamp_stream(pdf, page, font_name, label))


def _stamp_stream(
    pdf: pikepdf.Pdf, page: pikepdf.Page, font_name: pikepdf.Name, label: bytes
) -> pikepdf.Stream:
    box = page.cropbox
    left, bottom, right, top = (float(value) for value in box)
    rotation = int(page.obj.get(pikepdf.Name.Rotate, 0)) % 360
    if rotation not in (0, 90, 180, 270):
        rotation = 0
    width, height = right - left, top - bottom
    shown_width = height if rotation in (90, 270) else width
    matrix = _shown_to_user(rotation, left, bottom, right, top)

    size = min(_FONT_SIZE, (shown_width - 2 * _MARGIN) / max(1, len(label) * _SAFE_EM))
    size = max(_MIN_FONT_SIZE, size)
    x = max(_MARGIN / 2, (shown_width - len(label) * size * _AVERAGE_EM) / 2)
    steps: tuple[tuple[list[pikepdf.Object | float], str], ...] = (
        ([], "q"),
        (list(matrix), "cm"),
        (list(_GRAY), "rg"),
        ([], "BT"),
        ([font_name, size], "Tf"),
        ([x, _BASELINE], "Td"),
        ([pikepdf.String(label)], "Tj"),
        ([], "ET"),
        ([], "Q"),
    )
    instructions = [
        pikepdf.ContentStreamInstruction(operands, pikepdf.Operator(operator))
        for operands, operator in steps
    ]
    return pikepdf.Stream(pdf, pikepdf.unparse_content_stream(instructions))


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
