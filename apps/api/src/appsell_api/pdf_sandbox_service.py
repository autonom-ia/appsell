from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path

from anyio import to_thread
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import Response

from .pdf_verifier import MAX_PDF_BYTES, PDF_MIME, PdfRejected, verified_pdf_file

app = FastAPI(title="AppSell isolated PDF parser", docs_url=None, redoc_url=None)
_PARSER_SLOTS = asyncio.Semaphore(2)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/normalize")
async def normalize(request: Request) -> Response:
    if _PARSER_SLOTS.locked():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"retry-after": "2"},
        )
    async with _PARSER_SLOTS:
        return await _normalize_request(request)


async def _normalize_request(request: Request) -> Response:
    if request.headers.get("content-type") != PDF_MIME:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
    try:
        expected_size = int(request.headers["x-appsell-byte-size"])
        expected_sha256 = request.headers["x-appsell-sha256"]
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT) from error
    if not 1 <= expected_size <= MAX_PDF_BYTES or len(expected_sha256) != 64:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)

    source_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="appsell-parser-input-", delete=False) as source:
            source_path = Path(source.name)
            digest = hashlib.sha256()
            received = 0
            async for chunk in request.stream():
                received += len(chunk)
                if received > expected_size or received > MAX_PDF_BYTES:
                    raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
                digest.update(chunk)
                source.write(chunk)
            source.flush()
            os.fsync(source.fileno())
        os.chmod(source_path, 0o600)
        if received != expected_size or digest.hexdigest() != expected_sha256:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
        normalized, page_count, ready_sha256 = await to_thread.run_sync(
            lambda: _normalize_file(source_path, expected_size, expected_sha256)
        )
        return Response(
            normalized,
            media_type=PDF_MIME,
            headers={
                "x-appsell-page-count": str(page_count),
                "x-appsell-sha256": ready_sha256,
                "cache-control": "no-store",
                "x-content-type-options": "nosniff",
            },
        )
    except HTTPException:
        raise
    except PdfRejected as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT) from error
    finally:
        if source_path is not None:
            source_path.unlink(missing_ok=True)


def _normalize_file(
    source: Path, expected_size: int, expected_sha256: str
) -> tuple[bytes, int, str]:
    with (
        source.open("rb") as payload,
        verified_pdf_file(
            payload,
            expected_size=expected_size,
            expected_sha256_hex=expected_sha256,
            declared_content_type=PDF_MIME,
            isolated_service_boundary=True,
        ) as verified,
    ):
        return (
            verified.ready_path.read_bytes(),
            verified.page_count,
            verified.ready_sha256_hex,
        )
