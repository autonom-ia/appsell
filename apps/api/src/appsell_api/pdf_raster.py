from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from .pdf_verifier import PDF_MIME, PdfRejected, verified_pdf_file

MAX_RASTER_BYTES = 8 * 1024 * 1024
MAX_RASTER_DIMENSION = 1_600
MAX_WATERMARK_CHARACTERS = 200
_PDFTOPPM_TIMEOUT_SECONDS = 12
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_FONT_PATHS = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
)


class PdfRasterRejected(ValueError):
    pass


class PdfRasterUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class RenderedPdfPage:
    payload: bytes
    byte_size: int
    sha256_hex: str
    source_sha256_hex: str
    page_count: int
    page_number: int
    width: int
    height: int


def normalize_watermark(value: str) -> str:
    if not value or len(value) > MAX_WATERMARK_CHARACTERS * 4:
        raise PdfRasterRejected("watermark is outside the allowed range")
    normalized = unicodedata.normalize("NFKC", value)
    normalized = " ".join(normalized.split())
    if not 1 <= len(normalized) <= MAX_WATERMARK_CHARACTERS:
        raise PdfRasterRejected("watermark is outside the allowed range")
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise PdfRasterRejected("watermark contains unsafe characters")
    return normalized


def render_pdf_page(
    source: BinaryIO,
    *,
    expected_size: int,
    expected_sha256_hex: str,
    page_number: int,
    watermark: str,
    temporary_directory: str | None = None,
) -> RenderedPdfPage:
    if not 1 <= page_number <= 5_000:
        raise PdfRasterRejected("page number is outside the allowed range")
    if not _SHA256.fullmatch(expected_sha256_hex):
        raise PdfRasterRejected("expected SHA-256 is invalid")
    safe_watermark = normalize_watermark(watermark)
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        raise PdfRasterUnavailable("pdftoppm is unavailable")

    try:
        with verified_pdf_file(
            source,
            expected_size=expected_size,
            expected_sha256_hex=expected_sha256_hex,
            declared_content_type=PDF_MIME,
            temporary_directory=temporary_directory,
            isolated_service_boundary=True,
        ) as verified:
            if page_number > verified.page_count:
                raise PdfRasterRejected("requested page does not exist")
            return _render_verified_page(
                verified.ready_path,
                source_sha256_hex=verified.source_sha256_hex,
                page_count=verified.page_count,
                page_number=page_number,
                watermark=safe_watermark,
                pdftoppm=pdftoppm,
                temporary_directory=temporary_directory,
            )
    except PdfRasterRejected:
        raise
    except PdfRejected as error:
        raise PdfRasterRejected("PDF verification failed") from error


def _render_verified_page(
    source_path: Path,
    *,
    source_sha256_hex: str,
    page_count: int,
    page_number: int,
    watermark: str,
    pdftoppm: str,
    temporary_directory: str | None,
) -> RenderedPdfPage:
    render_directory: Path | None = None
    try:
        render_directory = Path(
            tempfile.mkdtemp(prefix="appsell-pdf-raster-", dir=temporary_directory)
        )
        os.chmod(render_directory, 0o700)
        output_prefix = render_directory / "page"
        try:
            completed = subprocess.run(  # noqa: S603 - fixed binary and bounded arguments
                [
                    pdftoppm,
                    "-f",
                    str(page_number),
                    "-l",
                    str(page_number),
                    "-singlefile",
                    "-png",
                    "-scale-to",
                    str(MAX_RASTER_DIMENSION),
                    str(source_path),
                    str(output_prefix),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                timeout=_PDFTOPPM_TIMEOUT_SECONDS,
                close_fds=True,
                env=_raster_environment(),
            )
        except subprocess.TimeoutExpired as error:
            raise PdfRasterRejected("PDF page rasterization timed out") from error
        except OSError as error:
            raise PdfRasterUnavailable("pdftoppm could not start") from error
        if completed.returncode != 0 or len(completed.stderr) > 4_096:
            raise PdfRasterRejected("PDF page rasterization failed")

        raster_path = output_prefix.with_suffix(".png")
        if not raster_path.is_file() or raster_path.is_symlink():
            raise PdfRasterRejected("PDF page rasterization returned no image")
        if raster_path.stat().st_size > MAX_RASTER_BYTES:
            raise PdfRasterRejected("rasterized PDF page exceeds the output limit")
        payload, width, height = watermark_png_file(raster_path, watermark)
        if len(payload) > MAX_RASTER_BYTES:
            raise PdfRasterRejected("watermarked PDF page exceeds the output limit")
        return RenderedPdfPage(
            payload=payload,
            byte_size=len(payload),
            sha256_hex=hashlib.sha256(payload).hexdigest(),
            source_sha256_hex=source_sha256_hex,
            page_count=page_count,
            page_number=page_number,
            width=width,
            height=height,
        )
    finally:
        if render_directory is not None:
            for child in render_directory.iterdir():
                child.unlink(missing_ok=True)
            render_directory.rmdir()


def watermark_png_file(source_path: Path, watermark: str) -> tuple[bytes, int, int]:
    try:
        with Image.open(source_path) as source:
            if source.format != "PNG":
                raise PdfRasterRejected("rasterizer returned an unexpected image format")
            source.load()
            width = int(source.width)
            height = int(source.height)
            if (
                width < 1
                or height < 1
                or width > MAX_RASTER_DIMENSION
                or height > MAX_RASTER_DIMENSION
                or width * height > MAX_RASTER_DIMENSION**2
            ):
                raise PdfRasterRejected("rasterizer returned invalid image dimensions")
            image = source.convert("RGBA")
    except PdfRasterRejected:
        raise
    except (OSError, UnidentifiedImageError, ValueError) as error:
        raise PdfRasterRejected("rasterizer returned an invalid PNG") from error

    font_size = max(14, min(28, min(width, height) // 18))
    font = _load_font(font_size)
    text_box = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), watermark, font=font)
    text_width = max(1, int(text_box[2] - text_box[0]))
    text_height = max(1, int(text_box[3] - text_box[1]))
    tile = Image.new("RGBA", (text_width + 48, text_height + 32), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text(
        (24 - text_box[0], 16 - text_box[1]),
        watermark,
        font=font,
        fill=(22, 36, 52, 88),
        stroke_width=1,
        stroke_fill=(255, 255, 255, 48),
    )
    rotated = tile.rotate(28, expand=True, resample=Image.Resampling.BICUBIC)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    x_step = max(220, rotated.width + 80)
    y_step = max(150, rotated.height + 70)
    for row, y in enumerate(range(-rotated.height, height + rotated.height, y_step)):
        offset = -(x_step // 2) if row % 2 else 0
        for x in range(-rotated.width + offset, width + rotated.width, x_step):
            overlay.alpha_composite(rotated, (x, y))
    watermarked = Image.alpha_composite(image, overlay).convert("RGB")

    with tempfile.SpooledTemporaryFile(max_size=MAX_RASTER_BYTES + 1, mode="w+b") as output:
        watermarked.save(output, format="PNG", optimize=True, compress_level=9)
        byte_size = output.tell()
        if byte_size > MAX_RASTER_BYTES:
            raise PdfRasterRejected("watermarked PDF page exceeds the output limit")
        output.seek(0)
        return output.read(), width, height


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_PATHS:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default(size=size)


def _raster_environment() -> dict[str, str]:
    allowed = ("PATH", "LANG", "LC_ALL", "TMPDIR")
    environment = {name: os.environ[name] for name in allowed if name in os.environ}
    environment.update(
        {
            "HOME": "/nonexistent",
            "NO_PROXY": "*",
            "http_proxy": "",
            "https_proxy": "",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
        }
    )
    return environment
