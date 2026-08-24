from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO

import pytest
from PIL import Image

from appsell_api.config import Settings
from appsell_api.learning_pdf import (
    LearningPdfRejected,
    authorized_pdf_page,
    render_prepared_pdf_page,
)


def settings(root: Path) -> Settings:
    return Settings(
        environment="ci",
        private_learning_asset_root=str(root),
        pdf_sandbox_socket="/run/appsell-pdf/pdf.sock",
    )


def png_payload() -> bytes:
    output = BytesIO()
    Image.new("RGB", (320, 480), "white").save(
        output, format="PNG", optimize=False, compress_level=6
    )
    return output.getvalue()


def page_row(payload: bytes, **overrides: Any) -> dict[str, Any]:
    return {
        "page_number": 1,
        "byte_size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "pixel_width": 320,
        "pixel_height": 480,
        "storage_driver": "legacy_bundle",
        "object_key": "course/pages/1.png",
        "storage_bucket": "codex-demo",
        "storage_version_id": None,
        **overrides,
    }


def test_authorized_legacy_page_is_integrity_checked_and_ephemeral(tmp_path: Path) -> None:
    payload = png_payload()
    page = tmp_path / "course" / "pages" / "1.png"
    page.parent.mkdir(parents=True)
    page.write_bytes(payload)

    with authorized_pdf_page(page_row(payload), settings(tmp_path), None) as source:
        temporary = source
        assert source.read_bytes() == payload
        assert source.parent != page.parent

    assert not temporary.exists()


def test_authorized_legacy_page_rejects_traversal_and_hash_mismatch(tmp_path: Path) -> None:
    payload = png_payload()
    page = tmp_path / "course" / "pages" / "1.png"
    page.parent.mkdir(parents=True)
    page.write_bytes(payload)

    with pytest.raises(LearningPdfRejected, match="key"):
        with authorized_pdf_page(
            page_row(payload, object_key="../course/pages/1.png"), settings(tmp_path), None
        ):
            pass
    with pytest.raises(LearningPdfRejected, match="integrity"):
        with authorized_pdf_page(page_row(payload, sha256="0" * 64), settings(tmp_path), None):
            pass


class FakeStorage:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[dict[str, str]] = []

    @contextmanager
    def open_download(self, **kwargs: str) -> Iterator[BinaryIO]:
        self.calls.append(kwargs)
        yield BytesIO(self.payload)


def test_authorized_s3_page_is_pinned_to_exact_object_version(tmp_path: Path) -> None:
    payload = png_payload()
    storage = FakeStorage(payload)
    row = page_row(
        payload,
        storage_driver="s3",
        storage_bucket="appsell-ready",
        object_key="tenants/seller/assets/course/v7/pages/1.png",
        storage_version_id="version-7",
    )

    with authorized_pdf_page(row, settings(tmp_path), storage) as source:  # type: ignore[arg-type]
        assert source.read_bytes() == payload

    assert storage.calls == [
        {
            "bucket": "appsell-ready",
            "key": "tenants/seller/assets/course/v7/pages/1.png",
            "version_id": "version-7",
        }
    ]


def test_authorized_page_rejects_digest_valid_bytes_that_are_not_png(tmp_path: Path) -> None:
    fake_png = b"\x89PNG\r\n\x1a\nnot-a-real-image"
    page = tmp_path / "course" / "pages" / "1.png"
    page.parent.mkdir(parents=True)
    page.write_bytes(fake_png)

    with pytest.raises(LearningPdfRejected, match="PNG"):
        with authorized_pdf_page(page_row(fake_png), settings(tmp_path), None):
            pass


def test_render_adds_license_footer_without_covering_page_or_exposing_pdf_bytes(
    tmp_path: Path,
) -> None:
    payload = png_payload()
    page = tmp_path / "page.png"
    page.write_bytes(payload)
    row = page_row(payload)

    rendered = render_prepared_pdf_page(
        page,
        row,
        page_count=3,
        watermark="Licenciado para bu***@example.test • ID abcdef",
    )

    assert rendered.payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert rendered.payload != payload
    assert b"%PDF" not in rendered.payload
    assert rendered.page_count == 3
    assert rendered.page_number == 1
    assert rendered.sha256_hex == hashlib.sha256(rendered.payload).hexdigest()
    with Image.open(BytesIO(rendered.payload)) as image:
        assert image.size == (320, 516)
        assert image.getpixel((160, 240)) == (255, 255, 255)
        assert image.getpixel((160, 481)) == (226, 232, 240)
