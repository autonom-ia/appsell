from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO

import httpx
import pytest
from PIL import Image

from appsell_api import learning_pdf
from appsell_api.config import Settings
from appsell_api.learning_pdf import (
    LearningPdfRejected,
    LearningPdfUnavailable,
    authorized_pdf_source,
    inspect_pdf,
    render_pdf_page,
)


def settings(root: Path) -> Settings:
    return Settings(
        environment="ci",
        private_learning_asset_root=str(root),
        pdf_sandbox_socket="/run/appsell-pdf/pdf.sock",
    )


def asset_row(payload: bytes, **overrides: Any) -> dict[str, Any]:
    return {
        "media_type": "application/pdf",
        "byte_size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "storage_driver": "legacy_bundle",
        "file_name": "course.pdf",
        **overrides,
    }


def test_authorized_legacy_source_is_integrity_checked_and_ephemeral(tmp_path: Path) -> None:
    payload = b"%PDF-1.4\nprivate course\n%%EOF"
    (tmp_path / "course.pdf").write_bytes(payload)

    with authorized_pdf_source(asset_row(payload), settings(tmp_path), None) as source:
        temporary = source
        assert source.read_bytes() == payload
        assert source.parent != tmp_path

    assert not temporary.exists()


def test_authorized_legacy_source_rejects_traversal_and_hash_mismatch(tmp_path: Path) -> None:
    payload = b"%PDF-1.4\nprivate course\n%%EOF"
    (tmp_path / "course.pdf").write_bytes(payload)

    with pytest.raises(LearningPdfRejected, match="file name"):
        with authorized_pdf_source(
            asset_row(payload, file_name="../course.pdf"), settings(tmp_path), None
        ):
            pass
    with pytest.raises(LearningPdfRejected, match="integrity"):
        with authorized_pdf_source(
            asset_row(payload, sha256="0" * 64), settings(tmp_path), None
        ):
            pass


class FakeStorage:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[dict[str, str]] = []

    @contextmanager
    def open_download(self, **kwargs: str) -> Iterator[BinaryIO]:
        self.calls.append(kwargs)
        yield BytesIO(self.payload)


def test_authorized_s3_source_is_pinned_to_exact_object_version(tmp_path: Path) -> None:
    payload = b"%PDF-1.4\nversion pinned\n%%EOF"
    storage = FakeStorage(payload)
    row = asset_row(
        payload,
        storage_driver="s3",
        storage_bucket="appsell-ready",
        object_key="seller/course.pdf",
        storage_version_id="version-7",
    )

    with authorized_pdf_source(row, settings(tmp_path), storage) as source:  # type: ignore[arg-type]
        assert source.read_bytes() == payload

    assert storage.calls == [
        {
            "bucket": "appsell-ready",
            "key": "seller/course.pdf",
            "version_id": "version-7",
        }
    ]


def test_manifest_rejects_extra_or_invalid_sandbox_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "course.pdf"
    source.write_bytes(b"pdf")
    monkeypatch.setattr(
        learning_pdf,
        "_sandbox_request",
        lambda *_args, **_kwargs: httpx.Response(
            200, json={"page_count": 2, "unexpected": True}
        ),
    )

    with pytest.raises(LearningPdfUnavailable, match="invalid metadata"):
        inspect_pdf(source, settings(tmp_path))


def test_render_recomputes_png_digest_and_rejects_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "course.pdf"
    source.write_bytes(b"pdf")
    png = b"\x89PNG\r\n\x1a\npage"
    monkeypatch.setattr(
        learning_pdf,
        "_sandbox_request",
        lambda *_args, **_kwargs: httpx.Response(
            200,
            content=png,
            headers={
                "content-type": "image/png",
                "content-length": str(len(png)),
                "x-appsell-page-count": "2",
                "x-appsell-page-number": "1",
                "x-appsell-sha256": "0" * 64,
            },
        ),
    )

    with pytest.raises(LearningPdfUnavailable, match="invalid page data"):
        render_pdf_page(source, settings(tmp_path), page_number=1, watermark="user 12345678")


def test_render_rejects_digest_valid_bytes_that_are_not_a_png(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "course.pdf"
    source.write_bytes(b"pdf")
    fake_png = b"\x89PNG\r\n\x1a\nnot-a-real-image"
    digest = hashlib.sha256(fake_png).hexdigest()
    monkeypatch.setattr(
        learning_pdf,
        "_sandbox_request",
        lambda *_args, **_kwargs: httpx.Response(
            200,
            content=fake_png,
            headers={
                "content-type": "image/png",
                "content-length": str(len(fake_png)),
                "x-appsell-page-count": "1",
                "x-appsell-page-number": "1",
                "x-appsell-sha256": digest,
            },
        ),
    )

    with pytest.raises(LearningPdfUnavailable, match="invalid PNG"):
        render_pdf_page(source, settings(tmp_path), page_number=1, watermark="user 12345678")


def test_render_reencodes_png_and_strips_appended_original_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "course.pdf"
    source.write_bytes(b"pdf")
    clean = BytesIO()
    Image.new("RGB", (2, 2), "white").save(clean, format="PNG")
    poisoned = clean.getvalue() + b"%PDF-1.4\nprivate original\n%%EOF"
    digest = hashlib.sha256(poisoned).hexdigest()
    monkeypatch.setattr(
        learning_pdf,
        "_sandbox_request",
        lambda *_args, **_kwargs: httpx.Response(
            200,
            content=poisoned,
            headers={
                "content-type": "image/png",
                "content-length": str(len(poisoned)),
                "x-appsell-page-count": "1",
                "x-appsell-page-number": "1",
                "x-appsell-sha256": digest,
            },
        ),
    )

    rendered = render_pdf_page(
        source, settings(tmp_path), page_number=1, watermark="user 12345678"
    )

    assert rendered.payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"%PDF" not in rendered.payload
    assert rendered.payload != poisoned
    assert rendered.sha256_hex == hashlib.sha256(rendered.payload).hexdigest()
