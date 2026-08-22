from __future__ import annotations

import hashlib
import io
import os
import struct
import subprocess
import sys
import tarfile
import time
import zlib
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]

from appsell_api.pdf_derivatives import (
    MAX_PDF_DERIVATIVE_BYTES,
    MAX_PDF_JOB_TEMP_BYTES,
    MAX_PDF_PAGE_ARCHIVE_BYTES,
    MAX_PDF_PAGE_BYTES,
    PDF_PAGE_ARCHIVE_MIME,
    PdfDerivativeRejected,
    _canonical_png_bytes,
    _pdftoppm_page_path,
    build_pdf_page_archive,
    prepared_pdf_file,
    verified_page_archive,
)
from appsell_api.pdf_sandbox_service import app
from appsell_api.pdf_verifier import MAX_PDF_BYTES


def test_pdftoppm_page_path_uses_page_count_padding() -> None:
    prefix = Path("/tmp/page")  # noqa: S108
    assert _pdftoppm_page_path(prefix, page_number=1, page_count=9).name == "page-1.png"
    assert _pdftoppm_page_path(prefix, page_number=1, page_count=65).name == "page-01.png"
    assert _pdftoppm_page_path(prefix, page_number=65, page_count=65).name == "page-65.png"


@pytest.mark.asyncio
async def test_rasterize_endpoint_returns_one_attested_complete_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APPSELL_PDF_SANDBOX_RECYCLE_AFTER_JOB", raising=False)
    payload = valid_pdf(page_count=2)
    source_sha = hashlib.sha256(payload).hexdigest()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://sandbox.test"
    ) as client:
        response = await client.post(
            "/rasterize",
            content=payload,
            headers={
                "content-type": "application/pdf",
                "x-appsell-byte-size": str(len(payload)),
                "x-appsell-sha256": source_sha,
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == PDF_PAGE_ARCHIVE_MIME
    assert response.headers["x-appsell-page-count"] == "2"
    assert response.headers["x-appsell-source-sha256"] == source_sha
    assert response.headers["x-appsell-sha256"] == hashlib.sha256(response.content).hexdigest()
    with verified_page_archive(
        io.BytesIO(response.content),
        expected_size=len(response.content),
        expected_sha256_hex=response.headers["x-appsell-sha256"],
    ) as verified:
        assert verified.page_count == 2


def test_prepared_pdf_file_uses_real_uds_attestation_stream_and_recycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket_path = Path("/tmp") / f"appsell-pdf-{uuid4().hex}.sock"  # noqa: S108
    environment = os.environ.copy()
    environment["APPSELL_PDF_SANDBOX_RECYCLE_AFTER_JOB"] = "true"
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "appsell_api.pdf_sandbox_service:app",
            "--uds",
            str(socket_path),
            "--log-level",
            "warning",
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not socket_path.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert socket_path.exists(), process.communicate(timeout=1)

        source = tmp_path / "source.pdf"
        source.write_bytes(valid_pdf(page_count=2))
        monkeypatch.setenv("APPSELL_API_PDF_SANDBOX_SOCKET", str(socket_path))
        monkeypatch.delenv("APPSELL_API_PDF_SANDBOX_URL", raising=False)
        with prepared_pdf_file(source) as prepared:
            assert prepared.page_count == 2
            assert len(prepared.pages) == 2
            assert prepared.ready_pdf_path.read_bytes().startswith(b"%PDF-")

        assert process.wait(timeout=10) == 77
        output_text = process.stdout.read() if process.stdout else ""
        assert "appsell_pdf_worker_planned_recycle" in output_text
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        socket_path.unlink(missing_ok=True)


def test_rejects_oversized_png_header_before_pixel_decode() -> None:
    output = io.BytesIO()
    Image.new("RGB", (1, 1)).save(output, format="PNG")
    payload = bytearray(output.getvalue())
    payload[16:24] = struct.pack(">II", 2_000, 2_000)
    payload[29:33] = struct.pack(">I", zlib.crc32(payload[12:29]) & 0xFFFFFFFF)

    with pytest.raises(PdfDerivativeRejected, match="dimensions"):
        _canonical_png_bytes(bytes(payload))


def test_pdf_temp_contract_covers_near_limit_archive_and_runtime_tmpfs() -> None:
    assert MAX_PDF_PAGE_ARCHIVE_BYTES > MAX_PDF_DERIVATIVE_BYTES + MAX_PDF_BYTES
    assert MAX_PDF_JOB_TEMP_BYTES == (
        MAX_PDF_PAGE_ARCHIVE_BYTES + MAX_PDF_DERIVATIVE_BYTES + (3 * MAX_PDF_BYTES)
    )
    configured_tmpfs = 402_653_184
    assert configured_tmpfs > MAX_PDF_JOB_TEMP_BYTES
    root = Path(__file__).parents[3]
    tmpfs_contract = f"{'/' + 'tmp'}:size={configured_tmpfs}"
    assert tmpfs_contract in (root / "compose.yaml").read_text()
    assert (root / "deploy/easypanel/compose.runtime-preview.yaml").read_text().count(
        tmpfs_contract
    ) >= 2


def test_builds_and_revalidates_complete_deterministic_pdf_derivatives(
    tmp_path: Path,
) -> None:
    payload = valid_pdf(page_count=3)
    source_sha = hashlib.sha256(payload).hexdigest()
    archives = []

    for run in ("first", "second"):
        job = tmp_path / run
        job.mkdir(mode=0o700)
        source = job / "source.pdf"
        source.write_bytes(payload)
        archive = build_pdf_page_archive(
            source,
            expected_size=len(payload),
            expected_sha256_hex=source_sha,
            job_directory=job,
        )
        archives.append(archive.path.read_bytes())

        with archive.path.open("rb") as stored:
            with verified_page_archive(
                stored,
                expected_size=archive.byte_size,
                expected_sha256_hex=archive.sha256_hex,
                temporary_directory=str(tmp_path),
            ) as verified:
                assert verified.source_sha256_hex == source_sha
                assert verified.page_count == 3
                assert verified.total_page_bytes == sum(page.byte_size for page in verified.pages)
                assert [page.page_number for page in verified.pages] == [1, 2, 3]
                assert verified.ready_pdf_path.read_bytes().startswith(b"%PDF-")
                for page in verified.pages:
                    assert page.byte_size <= MAX_PDF_PAGE_BYTES
                    assert page.sha256_hex == hashlib.sha256(page.path.read_bytes()).hexdigest()
                    with Image.open(page.path) as image:
                        assert image.format == "PNG"
                        assert image.mode == "RGB"
                        assert max(image.size) <= 1_600

    assert archives[0] == archives[1]


def test_rejects_archive_with_unexpected_or_traversal_member(tmp_path: Path) -> None:
    archive_path = tmp_path / "hostile.tar"
    with tarfile.open(archive_path, mode="w") as archive:
        add_member(archive, "manifest.json", b"{}")
        add_member(archive, "ready.pdf", b"%PDF-1.7\n%%EOF\n")
        add_member(archive, "../outside.png", b"not-a-png")
    payload = archive_path.read_bytes()

    with pytest.raises(PdfDerivativeRejected, match="invalid|incomplete"):
        with verified_page_archive(
            io.BytesIO(payload),
            expected_size=len(payload),
            expected_sha256_hex=hashlib.sha256(payload).hexdigest(),
            temporary_directory=str(tmp_path),
        ):
            pass

    assert not (tmp_path / "outside.png").exists()


def test_rejects_archive_when_attested_digest_does_not_match(tmp_path: Path) -> None:
    payload = b"not-a-tar"
    with pytest.raises(PdfDerivativeRejected, match="integrity"):
        with verified_page_archive(
            io.BytesIO(payload),
            expected_size=len(payload),
            expected_sha256_hex="0" * 64,
            temporary_directory=str(tmp_path),
        ):
            pass


def add_member(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    archive.addfile(info, io.BytesIO(payload))


def valid_pdf(*, page_count: int) -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(320, 480), invariant=1)
    for page_number in range(1, page_count + 1):
        canvas.drawString(32, 430, f"Derivative contract page {page_number}")
        canvas.showPage()
    canvas.save()
    return output.getvalue()
