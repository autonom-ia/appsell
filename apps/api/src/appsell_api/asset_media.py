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
    PDF_MEDIA_TYPE: AssetMediaSpec("pdf", 10 * _MIB, False, True),
    VIDEO_MEDIA_TYPE: AssetMediaSpec("mp4", 100 * _MIB, False, False),
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
