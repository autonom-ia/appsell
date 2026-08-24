from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, BinaryIO

from PIL import Image, UnidentifiedImageError

from .config import Settings
from .pdf_raster import (
    PdfRasterRejected,
    license_footer_dimensions,
    license_footer_png_file,
    normalize_watermark,
)
from .storage_s3 import S3Storage, StorageError

PNG_MIME = "image/png"
MAX_PDF_PAGE_BYTES = 8 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_PDF_PAGE_DIMENSION = 1_600
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class LearningPdfUnavailable(RuntimeError):
    pass


class LearningPdfRejected(ValueError):
    pass


@dataclass(frozen=True)
class RenderedLearningPdfPage:
    payload: bytes
    page_count: int
    page_number: int
    sha256_hex: str


@contextmanager
def authorized_pdf_page(
    row: Mapping[str, Any], settings: Settings, storage: S3Storage | None
) -> Iterator[Path]:
    expected_size, expected_sha256, expected_width, expected_height = _page_metadata(row)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="appsell-learning-page-", suffix=".png", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.chmod(temporary_path, 0o600)
            if row.get("storage_driver") == "s3":
                if storage is None:
                    raise LearningPdfUnavailable("learning PDF page storage is unavailable")
                try:
                    with storage.open_download(
                        bucket=str(row["storage_bucket"]),
                        key=str(row["object_key"]),
                        version_id=str(row["storage_version_id"]),
                    ) as source:
                        _copy_verified(
                            source,
                            temporary.file,
                            expected_size,
                            expected_sha256,
                            maximum=MAX_PDF_PAGE_BYTES,
                        )
                except StorageError as error:
                    raise LearningPdfUnavailable(
                        "learning PDF page storage is unavailable"
                    ) from error
            elif row.get("storage_driver") == "legacy_bundle":
                source_path = _legacy_page_path(str(row.get("object_key") or ""), settings)
                with source_path.open("rb") as source:
                    _copy_verified(
                        source,
                        temporary.file,
                        expected_size,
                        expected_sha256,
                        maximum=MAX_PDF_PAGE_BYTES,
                    )
            else:
                raise LearningPdfRejected("learning PDF page storage driver is invalid")
            temporary.flush()
            os.fsync(temporary.fileno())
        _verify_png(temporary_path, expected_width, expected_height)
        yield temporary_path
    except (LearningPdfRejected, LearningPdfUnavailable):
        raise
    except (OSError, TypeError, ValueError) as error:
        raise LearningPdfUnavailable("learning PDF page is unavailable") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def render_prepared_pdf_page(
    path: Path,
    row: Mapping[str, Any],
    *,
    page_count: int,
    watermark: str,
) -> RenderedLearningPdfPage:
    try:
        page_number = int(row["page_number"])
    except (KeyError, TypeError, ValueError) as error:
        raise LearningPdfRejected("PDF page metadata is invalid") from error
    if not 1 <= page_number <= page_count <= MAX_PDF_PAGES:
        raise LearningPdfRejected("PDF page metadata is invalid")
    try:
        payload, width, height = license_footer_png_file(path, normalize_watermark(watermark))
    except PdfRasterRejected as error:
        raise LearningPdfRejected("PDF page licensing was rejected") from error
    expected_width, expected_height, _, _ = license_footer_dimensions(
        int(row["pixel_width"]), int(row["pixel_height"])
    )
    if width != expected_width or height != expected_height:
        raise LearningPdfRejected("PDF page dimensions drifted")
    if not 1 <= len(payload) <= MAX_PDF_PAGE_BYTES:
        raise LearningPdfUnavailable("licensed PDF page exceeds the output limit")
    return RenderedLearningPdfPage(
        payload=payload,
        page_count=page_count,
        page_number=page_number,
        sha256_hex=hashlib.sha256(payload).hexdigest(),
    )


def _page_metadata(row: Mapping[str, Any]) -> tuple[int, str, int, int]:
    try:
        expected_size = int(row["byte_size"])
        expected_sha256 = str(row["sha256"])
        width = int(row["pixel_width"])
        height = int(row["pixel_height"])
    except (KeyError, TypeError, ValueError) as error:
        raise LearningPdfRejected("learning PDF page metadata is invalid") from error
    if (
        not 1 <= expected_size <= MAX_PDF_PAGE_BYTES
        or not _SHA256.fullmatch(expected_sha256)
        or not 1 <= width <= MAX_PDF_PAGE_DIMENSION
        or not 1 <= height <= MAX_PDF_PAGE_DIMENSION
        or width * height > MAX_PDF_PAGE_DIMENSION**2
    ):
        raise LearningPdfRejected("learning PDF page metadata is invalid")
    return expected_size, expected_sha256, width, height


def _verify_png(path: Path, expected_width: int, expected_height: int) -> None:
    try:
        with Image.open(path) as image:
            if (
                image.format != "PNG"
                or image.mode != "RGB"
                or image.width != expected_width
                or image.height != expected_height
            ):
                raise LearningPdfRejected("learning PDF page PNG is invalid")
            image.load()
    except LearningPdfRejected:
        raise
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError) as error:
        raise LearningPdfRejected("learning PDF page PNG is invalid") from error


def _legacy_page_path(object_key: str, settings: Settings) -> Path:
    if (
        not object_key
        or Path(object_key).is_absolute()
        or "\x00" in object_key
        or any(part in {"", ".", ".."} for part in object_key.split("/"))
    ):
        raise LearningPdfRejected("legacy PDF page key is invalid")
    configured = settings.private_learning_asset_root
    root = Path(configured) if configured else Path.cwd() / "output" / "pdf" / "codex"
    root = root.resolve(strict=True)
    candidate = (root / object_key).resolve(strict=True)
    if root not in candidate.parents or not candidate.is_file() or candidate.is_symlink():
        raise LearningPdfRejected("legacy PDF page is unavailable")
    return candidate


def _copy_verified(
    source: BinaryIO,
    destination: IO[bytes],
    expected_size: int,
    expected_sha256: str,
    *,
    maximum: int,
) -> None:
    received = 0
    digest = hashlib.sha256()
    while chunk := source.read(64 * 1024):
        received += len(chunk)
        if received > expected_size or received > maximum:
            raise LearningPdfRejected("learning PDF page size does not match metadata")
        digest.update(chunk)
        destination.write(chunk)
    if received != expected_size or digest.hexdigest() != expected_sha256:
        raise LearningPdfRejected("learning PDF page integrity does not match metadata")
