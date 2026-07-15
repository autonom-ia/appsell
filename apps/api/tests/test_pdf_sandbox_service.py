from __future__ import annotations

import asyncio
import hashlib
import io
import struct

import httpx
import pytest
from fastapi import Response
from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]

from appsell_api import pdf_sandbox_service
from appsell_api.pdf_sandbox_service import app


@pytest.mark.asyncio
async def test_isolated_parser_normalizes_bounded_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPSELL_API_PDF_SANDBOX_URL", raising=False)
    payload = valid_pdf()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        response = await client.post(
            "/normalize",
            content=payload,
            headers={
                "content-type": "application/pdf",
                "x-appsell-byte-size": str(len(payload)),
                "x-appsell-sha256": hashlib.sha256(payload).hexdigest(),
            },
        )

    assert response.status_code == 200
    assert response.headers["x-appsell-page-count"] == "1"
    assert response.headers["cache-control"] == "no-store"
    assert response.content != payload
    assert response.content.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_isolated_parser_rejects_size_mismatch() -> None:
    payload = valid_pdf()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        response = await client.post(
            "/normalize",
            content=payload,
            headers={
                "content-type": "application/pdf",
                "x-appsell-byte-size": str(len(payload) - 1),
                "x-appsell-sha256": hashlib.sha256(payload).hexdigest(),
            },
        )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_isolated_parser_rejects_overload_without_starting_third_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    two_started = asyncio.Event()
    release = asyncio.Event()
    starts = 0

    async def slow_normalize(_request: object) -> Response:
        nonlocal starts
        starts += 1
        if starts == 2:
            two_started.set()
        await release.wait()
        return Response(b"ok", status_code=200)

    monkeypatch.setattr(pdf_sandbox_service, "_normalize_request", slow_normalize)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        first = asyncio.create_task(client.post("/normalize"))
        second = asyncio.create_task(client.post("/normalize"))
        await asyncio.wait_for(two_started.wait(), timeout=1)
        overloaded = await client.post("/normalize")
        release.set()
        await asyncio.gather(first, second)

    assert starts == 2
    assert overloaded.status_code == 429
    assert overloaded.headers["retry-after"] == "2"


@pytest.mark.asyncio
async def test_isolated_service_probes_bounded_video_without_returning_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = valid_mp4()
    probed: list[bytes] = []

    def record_probe(path: object) -> None:
        probed.append(path.read_bytes())  # type: ignore[union-attr]

    monkeypatch.setattr(pdf_sandbox_service, "probe_mp4_file", record_probe)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        response = await client.post(
            "/probe-video",
            content=payload,
            headers={
                "content-type": "video/mp4",
                "x-appsell-byte-size": str(len(payload)),
                "x-appsell-sha256": hashlib.sha256(payload).hexdigest(),
            },
        )

    assert response.status_code == 204
    assert response.content == b""
    assert probed == [payload]


@pytest.mark.asyncio
async def test_isolated_video_probe_rejects_hash_mismatch_before_ffprobe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = valid_mp4()
    called = False

    def record_probe(_path: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(pdf_sandbox_service, "probe_mp4_file", record_probe)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        response = await client.post(
            "/probe-video",
            content=payload,
            headers={
                "content-type": "video/mp4",
                "x-appsell-byte-size": str(len(payload)),
                "x-appsell-sha256": "0" * 64,
            },
        )

    assert response.status_code == 422
    assert called is False


@pytest.mark.asyncio
async def test_isolated_video_probe_reports_missing_binary_as_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = valid_mp4()

    def unavailable(_path: object) -> None:
        raise pdf_sandbox_service.VideoProbeUnavailable("missing ffprobe")

    monkeypatch.setattr(pdf_sandbox_service, "probe_mp4_file", unavailable)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        response = await client.post(
            "/probe-video",
            content=payload,
            headers={
                "content-type": "video/mp4",
                "x-appsell-byte-size": str(len(payload)),
                "x-appsell-sha256": hashlib.sha256(payload).hexdigest(),
            },
        )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_video_probe_allows_only_one_maximum_sized_tempfile_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_probe(_request: object) -> Response:
        started.set()
        await release.wait()
        return Response(status_code=204)

    monkeypatch.setattr(pdf_sandbox_service, "_probe_video_request", slow_probe)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        first = asyncio.create_task(client.post("/probe-video"))
        await asyncio.wait_for(started.wait(), timeout=1)
        overloaded = await client.post("/probe-video")
        release.set()
        completed = await first

    assert completed.status_code == 204
    assert overloaded.status_code == 429
    assert overloaded.headers["retry-after"] == "2"


def valid_pdf() -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(320, 480), invariant=1)
    canvas.drawString(32, 430, "Isolated parser contract")
    canvas.showPage()
    canvas.save()
    return output.getvalue()


def valid_mp4() -> bytes:
    def box(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I4s", len(payload) + 8, kind) + payload

    return (
        box(b"ftyp", b"isom\0\0\0\0isommp42") + box(b"moov", b"metadata") + box(b"mdat", b"video")
    )
