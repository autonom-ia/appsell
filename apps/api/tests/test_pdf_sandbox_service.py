from __future__ import annotations

import asyncio
import hashlib
import io

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


def valid_pdf() -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(320, 480), invariant=1)
    canvas.drawString(32, 430, "Isolated parser contract")
    canvas.showPage()
    canvas.save()
    return output.getvalue()
