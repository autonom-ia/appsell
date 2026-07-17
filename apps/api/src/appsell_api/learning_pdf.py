from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, BinaryIO

import httpx
from PIL import Image, UnidentifiedImageError

from .config import Settings
from .pdf_verifier import MAX_PDF_BYTES, PDF_MIME
from .storage_s3 import S3Storage, StorageError

PNG_MIME = "image/png"
MAX_PDF_PAGE_BYTES = 8 * 1024 * 1024
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
def authorized_pdf_source(
    row: Mapping[str, Any], settings: Settings, storage: S3Storage | None
) -> Iterator[Path]:
    if row.get("media_type") != PDF_MIME:
        raise LearningPdfRejected("learning asset is not a PDF")
    try:
        expected_size = int(row["byte_size"])
        expected_sha256 = str(row["sha256"])
    except (KeyError, TypeError, ValueError) as error:
        raise LearningPdfRejected("learning PDF metadata is invalid") from error
    if not 1 <= expected_size <= MAX_PDF_BYTES or not _SHA256.fullmatch(expected_sha256):
        raise LearningPdfRejected("learning PDF metadata is invalid")

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="appsell-learning-pdf-", suffix=".pdf", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.chmod(temporary_path, 0o600)
            if row.get("storage_driver") == "s3":
                if storage is None:
                    raise LearningPdfUnavailable("learning PDF storage is unavailable")
                try:
                    with storage.open_download(
                        bucket=str(row["storage_bucket"]),
                        key=str(row["object_key"]),
                        version_id=str(row["storage_version_id"]),
                    ) as source:
                        _copy_verified(source, temporary.file, expected_size, expected_sha256)
                except StorageError as error:
                    raise LearningPdfUnavailable("learning PDF storage is unavailable") from error
            elif row.get("storage_driver") == "legacy_bundle":
                source_path = _legacy_source_path(str(row.get("file_name") or ""), settings)
                with source_path.open("rb") as source:
                    _copy_verified(source, temporary.file, expected_size, expected_sha256)
            else:
                raise LearningPdfRejected("learning PDF storage driver is invalid")
            temporary.flush()
            os.fsync(temporary.fileno())
        yield temporary_path
    except (LearningPdfRejected, LearningPdfUnavailable):
        raise
    except (OSError, ValueError) as error:
        raise LearningPdfUnavailable("learning PDF is unavailable") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def inspect_pdf(path: Path, settings: Settings) -> int:
    response = _sandbox_request(path, settings, endpoint_path="/inspect", watermark=None)
    try:
        payload = response.json()
        page_count = int(payload["page_count"])
    except (KeyError, TypeError, ValueError) as error:
        raise LearningPdfUnavailable("PDF sandbox returned invalid metadata") from error
    if set(payload) != {"page_count"} or not 1 <= page_count <= 5_000:
        raise LearningPdfUnavailable("PDF sandbox returned invalid metadata")
    return page_count


def render_pdf_page(
    path: Path, settings: Settings, *, page_number: int, watermark: str
) -> RenderedLearningPdfPage:
    if not 1 <= page_number <= 5_000 or not re.fullmatch(r"[a-z0-9 -]{8,120}", watermark):
        raise LearningPdfRejected("PDF page request is invalid")
    response = _sandbox_request(
        path,
        settings,
        endpoint_path=f"/render-page/{page_number}",
        watermark=watermark,
    )
    try:
        page_count = int(response.headers["x-appsell-page-count"])
        returned_page = int(response.headers["x-appsell-page-number"])
        sha256_hex = response.headers["x-appsell-sha256"]
        content_length = int(response.headers["content-length"])
    except (KeyError, ValueError) as error:
        raise LearningPdfUnavailable("PDF sandbox returned invalid page metadata") from error
    payload = response.content
    if (
        response.headers.get("content-type") != PNG_MIME
        or not 1 <= page_count <= 5_000
        or returned_page != page_number
        or not _SHA256.fullmatch(sha256_hex)
        or content_length != len(payload)
        or not 1 <= len(payload) <= MAX_PDF_PAGE_BYTES
        or hashlib.sha256(payload).hexdigest() != sha256_hex
    ):
        raise LearningPdfUnavailable("PDF sandbox returned invalid page data")
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if (
                image.format != "PNG"
                or image.mode != "RGB"
                or not 1 <= image.width <= 1_600
                or not 1 <= image.height <= 1_600
                or image.width * image.height > 1_600**2
            ):
                raise LearningPdfUnavailable("PDF sandbox returned invalid PNG")
            image.load()
            sanitized = io.BytesIO()
            image.save(sanitized, format="PNG", optimize=False)
            sanitized_payload = sanitized.getvalue()
            if not 1 <= len(sanitized_payload) <= MAX_PDF_PAGE_BYTES:
                raise LearningPdfUnavailable("sanitized PDF page exceeds the output limit")
    except LearningPdfUnavailable:
        raise
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError) as error:
        raise LearningPdfUnavailable("PDF sandbox returned invalid PNG") from error
    return RenderedLearningPdfPage(
        payload=sanitized_payload,
        page_count=page_count,
        page_number=returned_page,
        sha256_hex=hashlib.sha256(sanitized_payload).hexdigest(),
    )


def _sandbox_request(
    path: Path,
    settings: Settings,
    *,
    endpoint_path: str,
    watermark: str | None,
) -> httpx.Response:
    size = path.stat().st_size
    sha256_hex = _file_sha256(path)
    base_url = "http://pdf-sandbox"
    transport: httpx.BaseTransport | None = None
    if settings.pdf_sandbox_socket:
        transport = httpx.HTTPTransport(uds=settings.pdf_sandbox_socket, retries=0)
    elif settings.pdf_sandbox_url:
        parsed = httpx.URL(settings.pdf_sandbox_url)
        base_url = f"{parsed.scheme}://{parsed.host}"
        if parsed.port is not None:
            base_url += f":{parsed.port}"
    else:
        raise LearningPdfUnavailable("PDF sandbox is not configured")
    headers = {
        "content-type": PDF_MIME,
        "x-appsell-byte-size": str(size),
        "x-appsell-sha256": sha256_hex,
        **({"x-appsell-watermark": watermark} if watermark is not None else {}),
    }
    response: httpx.Response | None = None
    last_error: Exception | None = None
    with httpx.Client(
        transport=transport,
        timeout=httpx.Timeout(20.0, connect=3.0),
        trust_env=False,
        follow_redirects=False,
    ) as client:
        for delay in (0.0, 0.2, 0.5, 1.0):
            if delay:
                time.sleep(delay)
            try:
                with path.open("rb") as source:
                    candidate = client.post(
                        f"{base_url}{endpoint_path}", headers=headers, content=source
                    )
            except (httpx.HTTPError, OSError) as error:
                last_error = error
                continue
            response = candidate
            if candidate.status_code < 500:
                break
    if response is None:
        raise LearningPdfUnavailable("PDF sandbox is unavailable") from last_error
    if response.status_code in {413, 415, 422}:
        raise LearningPdfRejected("PDF sandbox rejected the document")
    if response.status_code == 429:
        raise LearningPdfUnavailable("PDF sandbox is busy")
    if response.status_code != 200:
        raise LearningPdfUnavailable("PDF sandbox is unavailable")
    if response.headers.get("x-appsell-worker-recycle") != "1":
        raise LearningPdfUnavailable("PDF sandbox did not attest worker recycling")
    return response


def _legacy_source_path(file_name: str, settings: Settings) -> Path:
    if not file_name or file_name != Path(file_name).name or "\x00" in file_name:
        raise LearningPdfRejected("legacy PDF file name is invalid")
    configured = settings.private_learning_asset_root
    root = Path(configured) if configured else Path.cwd() / "output" / "pdf" / "codex"
    root = root.resolve(strict=True)
    candidate = (root / file_name).resolve(strict=True)
    if candidate.parent != root or not candidate.is_file() or candidate.is_symlink():
        raise LearningPdfRejected("legacy PDF is unavailable")
    return candidate


def _copy_verified(
    source: BinaryIO, destination: IO[bytes], expected_size: int, expected_sha256: str
) -> None:
    received = 0
    digest = hashlib.sha256()
    while chunk := source.read(64 * 1024):
        received += len(chunk)
        if received > expected_size or received > MAX_PDF_BYTES:
            raise LearningPdfRejected("learning PDF size does not match metadata")
        digest.update(chunk)
        destination.write(chunk)
    if received != expected_size or digest.hexdigest() != expected_sha256:
        raise LearningPdfRejected("learning PDF integrity does not match metadata")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
