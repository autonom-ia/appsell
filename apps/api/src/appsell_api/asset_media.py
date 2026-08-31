"""The accepted asset media, in one place.

Before this module the media contract was restated in the request model, the
presign limits, the delivery headers, the worker branch and three SQL
expressions, none of them derived from the others. A format accepted in one
place and forgotten in another fails late, in production, with a message that
blames the wrong layer.

Documents are support material and are always handed over as a download. They
are never rendered inside the page, so nothing in the player has to decide how
to display a spreadsheet, and a malformed one cannot become a rendering problem.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

PDF_MEDIA_TYPE: Final = "application/pdf"
VIDEO_MEDIA_TYPE: Final = "video/mp4"
PAGE_IMAGE_MEDIA_TYPE: Final = "image/png"

DOCX_MEDIA_TYPE: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MEDIA_TYPE: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX_MEDIA_TYPE: Final = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@dataclass(frozen=True)
class AssetMediaSpec:
    extension: str
    max_bytes: int
    support_material_only: bool
    downloadable: bool


_MIB: Final = 1024 * 1024

SUPPORTED_MEDIA: Final[dict[str, AssetMediaSpec]] = {
    PDF_MEDIA_TYPE: AssetMediaSpec("pdf", 100 * _MIB, False, True),
    # 512 MiB, e não 100: uma aula de 45 min em 1080p passa de 800 MB, e uma de
    # 30 min fica perto de 500. Com o teto antigo o vendedor não publicava aula
    # de duração real. Só foi possível subir depois do envio em partes, porque
    # antes o `PUT` único não entregava o arquivo dentro do prazo da assinatura.
    VIDEO_MEDIA_TYPE: AssetMediaSpec("mp4", 512 * _MIB, False, False),
    DOCX_MEDIA_TYPE: AssetMediaSpec("docx", 20 * _MIB, True, True),
    XLSX_MEDIA_TYPE: AssetMediaSpec("xlsx", 20 * _MIB, True, True),
    PPTX_MEDIA_TYPE: AssetMediaSpec("pptx", 40 * _MIB, True, True),
    "text/plain": AssetMediaSpec("txt", 2 * _MIB, True, True),
    "text/markdown": AssetMediaSpec("md", 2 * _MIB, True, True),
    "text/csv": AssetMediaSpec("csv", 5 * _MIB, True, True),
    "application/json": AssetMediaSpec("json", 5 * _MIB, True, True),
}

DOCUMENT_MEDIA_TYPES: Final = frozenset(
    media for media, spec in SUPPORTED_MEDIA.items() if spec.support_material_only
)
DOWNLOADABLE_MEDIA_TYPES: Final = frozenset(
    media for media, spec in SUPPORTED_MEDIA.items() if spec.downloadable
)
MAX_ASSET_BYTES: Final = max(spec.max_bytes for spec in SUPPORTED_MEDIA.values())

#: Os tetos do vídeo referenciado de dentro de um PDF.
#:
#: Não são o teto do catálogo, e a Decisão 010 exige a justificativa: são
#: caminhos diferentes. O material de aula sobe do navegador do vendedor, num
#: `PUT` único com URL assinada de 300 s, e o teto ali é o que o vendedor
#: consegue entregar, não o que aceitaríamos guardar. Este é baixado por nós,
#: com prazo nosso de 15 min.
#:
#: São dois números e não um: a origem é o que aceitamos baixar, o derivado é o
#: que aceitamos guardar e servir. Coincidem hoje e não são o mesmo fato. Moram
#: aqui, e não em `video_remux`, porque `pdf_link_video` precisa do mesmo número
#: e não pode importar aquele módulo: ele executa `ffmpeg` e é alcançável só a
#: partir do serviço isolado.
PDF_LINK_VIDEO_MAX_SOURCE_BYTES: Final = 512 * _MIB
PDF_LINK_VIDEO_MAX_DERIVATIVE_BYTES: Final = 512 * _MIB


def spec_for(media_type: str) -> AssetMediaSpec | None:
    return SUPPORTED_MEDIA.get(media_type)


def is_document(media_type: str) -> bool:
    return media_type in DOCUMENT_MEDIA_TYPES


def max_bytes(media_type: str) -> int:
    spec = SUPPORTED_MEDIA.get(media_type)
    return spec.max_bytes if spec else 0


def extension_matches(media_type: str, file_name: str) -> bool:
    spec = SUPPORTED_MEDIA.get(media_type)
    if spec is None:
        return False
    return file_name.lower().endswith(f".{spec.extension}")
