from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from anyio import to_thread
from fastapi import FastAPI, HTTPException, Request, status
from fastapi import Path as PathParameter
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTasks

from .pdf_derivatives import (
    PDF_PAGE_ARCHIVE_MIME,
    PdfDerivativeRejected,
    build_pdf_page_archive,
)
from .pdf_links import PdfLinksRejected, extract_pdf_link_manifest
from .pdf_raster import (
    PdfRasterRejected,
    PdfRasterUnavailable,
    RenderedPdfPage,
    render_pdf_page,
)
from .pdf_verifier import MAX_PDF_BYTES, PDF_MIME, PdfRejected, verified_pdf_file
from .video_remux import (
    MAX_DERIVATIVE_BYTES as MAX_REMUX_BYTES,
)
from .video_remux import (
    VideoRemuxRejected,
    VideoRemuxUnavailable,
    remux_to_mp4,
)
from .video_verifier import (
    MAX_VIDEO_BYTES,
    VIDEO_MP4_MIME,
    VideoProbeUnavailable,
    VideoRejected,
    _validate_mp4_container,
    probe_mp4_file,
)


class _CleanupStreamingResponse(StreamingResponse):
    """Uma resposta em fluxo que sempre limpa, mesmo se o cliente sumir.

    O `finally` dentro do gerador não basta: quando o envio falha por
    desconexão, o Starlette não fecha o gerador sozinho, e se a desconexão
    acontece antes da primeira iteração o corpo dele nunca roda. Uma
    `BackgroundTask` também não serve — ela só executa se a resposta terminar.

    Isso importava mais do que o diretório deixado para trás: a reciclagem
    também ficava sem rodar, e com `_DRAINING` já ligado o serviço passaria a
    responder 503 para sempre.
    """

    def __init__(self, *args: object, cleanup: Callable[[], None], **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._cleanup = cleanup

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._cleanup()


app = FastAPI(title="AppSell isolated PDF parser", docs_url=None, redoc_url=None)
_MEDIA_SLOT = asyncio.Semaphore(1)
_DRAINING = asyncio.Event()


@app.middleware("http")
async def recycle_after_media_job(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    if (
        request.method != "POST"
        or not _recycle_enabled()
        or not bool(getattr(request.state, "recycle_owner", False))
    ):
        return response
    response.headers["x-appsell-worker-recycle"] = "1"
    tasks = BackgroundTasks()
    if response.background is not None:
        tasks.tasks.append(response.background)
    tasks.add_task(_terminate_sandbox_process)
    response.background = tasks
    return response


def _recycle_enabled() -> bool:
    return os.environ.get("APPSELL_PDF_SANDBOX_RECYCLE_AFTER_JOB") == "true"


def _terminate_sandbox_process() -> None:
    # Starlette invokes background tasks after handing the final body chunk to
    # the ASGI server, but the kernel socket may still contain buffered bytes.
    # Give Uvicorn a bounded drain window before the deliberate hard recycle.
    time.sleep(0.5)
    os.write(1, b'{"event":"appsell_pdf_worker_planned_recycle"}\n')
    os._exit(77)


@app.get("/health")
async def health() -> dict[str, str]:
    if _DRAINING.is_set() or shutil.which("ffprobe") is None or shutil.which("pdftoppm") is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return {"status": "ok"}


@app.post("/normalize")
async def normalize(request: Request) -> Response:
    _reject_busy_or_draining()
    async with _MEDIA_SLOT:
        _reject_if_draining()
        try:
            return await _normalize_request(request)
        finally:
            _begin_draining_after_job(request)


def _reject_busy_or_draining() -> None:
    _reject_if_draining()
    if _MEDIA_SLOT.locked():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"retry-after": "2"},
        )


def _reject_if_draining() -> None:
    if _DRAINING.is_set():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"retry-after": "1"},
        )


def _begin_draining_after_job(request: Request) -> None:
    if _recycle_enabled():
        request.state.recycle_owner = True
        _DRAINING.set()


@app.post("/inspect")
async def inspect(request: Request) -> Response:
    _reject_busy_or_draining()
    async with _MEDIA_SLOT:
        _reject_if_draining()
        try:
            return await _inspect_request(request)
        finally:
            _begin_draining_after_job(request)


@app.post("/links")
async def links(request: Request) -> Response:
    _reject_busy_or_draining()
    async with _MEDIA_SLOT:
        _reject_if_draining()
        try:
            return await _links_request(request)
        finally:
            _begin_draining_after_job(request)


@app.post("/render-page/{page_number}")
async def render_page(
    request: Request,
    page_number: int = PathParameter(ge=1, le=5_000),
) -> Response:
    _reject_busy_or_draining()
    async with _MEDIA_SLOT:
        _reject_if_draining()
        try:
            return await _render_page_request(request, page_number)
        finally:
            _begin_draining_after_job(request)


@app.post("/rasterize")
async def rasterize(request: Request) -> Response:
    _reject_busy_or_draining()
    async with _MEDIA_SLOT:
        _reject_if_draining()
        try:
            return await _rasterize_request(request)
        finally:
            _begin_draining_after_job(request)


@app.post("/prepare-video")
async def prepare_video(request: Request) -> Response:
    _reject_busy_or_draining()
    async with _MEDIA_SLOT:
        _reject_if_draining()
        try:
            return await _prepare_video_request(request)
        finally:
            _begin_draining_after_job(request)


@app.post("/probe-video", status_code=status.HTTP_204_NO_CONTENT)
async def probe_video(request: Request) -> Response:
    _reject_busy_or_draining()
    async with _MEDIA_SLOT:
        _reject_if_draining()
        try:
            return await _probe_video_request(request)
        finally:
            _begin_draining_after_job(request)


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

    job_directory = _private_job_directory("appsell-normalize-job-")
    source_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="source-", suffix=".pdf", dir=job_directory, delete=False
        ) as source:
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
            lambda: _normalize_file(source_path, expected_size, expected_sha256, job_directory)
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
        shutil.rmtree(job_directory)


async def _rasterize_request(request: Request) -> Response:
    if request.headers.get("content-type") != PDF_MIME:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
    try:
        expected_size = int(request.headers["x-appsell-byte-size"])
        expected_sha256 = request.headers["x-appsell-sha256"]
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT) from error
    if not 1 <= expected_size <= MAX_PDF_BYTES or not re.fullmatch(
        r"[0-9a-f]{64}", expected_sha256
    ):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)

    job_directory = _private_job_directory("appsell-raster-archive-job-")
    source_path = job_directory / "source.pdf"
    handed_off = False
    try:
        digest = hashlib.sha256()
        received = 0
        with source_path.open("xb") as source:
            os.chmod(source_path, 0o600)
            async for chunk in request.stream():
                received += len(chunk)
                if received > expected_size or received > MAX_PDF_BYTES:
                    raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
                digest.update(chunk)
                source.write(chunk)
            source.flush()
            os.fsync(source.fileno())
        if received != expected_size or digest.hexdigest() != expected_sha256:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
        archive = await to_thread.run_sync(
            lambda: build_pdf_page_archive(
                source_path,
                expected_size=expected_size,
                expected_sha256_hex=expected_sha256,
                job_directory=job_directory,
            )
        )
        source_path.unlink(missing_ok=True)
        tasks = BackgroundTasks()
        tasks.add_task(shutil.rmtree, job_directory)
        handed_off = True
        return FileResponse(
            archive.path,
            media_type=PDF_PAGE_ARCHIVE_MIME,
            background=tasks,
            headers={
                "cache-control": "no-store",
                "x-appsell-page-count": str(archive.page_count),
                "x-appsell-total-page-bytes": str(archive.total_page_bytes),
                "x-appsell-sha256": archive.sha256_hex,
                "x-appsell-source-sha256": archive.source_sha256_hex,
                "x-content-type-options": "nosniff",
            },
        )
    except HTTPException:
        raise
    except (PdfDerivativeRejected, PdfRejected) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT) from error
    finally:
        if not handed_off:
            shutil.rmtree(job_directory, ignore_errors=True)


def _normalize_file(
    source: Path, expected_size: int, expected_sha256: str, job_directory: Path
) -> tuple[bytes, int, str]:
    with (
        source.open("rb") as payload,
        verified_pdf_file(
            payload,
            expected_size=expected_size,
            expected_sha256_hex=expected_sha256,
            declared_content_type=PDF_MIME,
            temporary_directory=str(job_directory),
            isolated_service_boundary=True,
        ) as verified,
    ):
        return (
            verified.ready_path.read_bytes(),
            verified.page_count,
            verified.ready_sha256_hex,
        )


async def _inspect_request(request: Request) -> Response:
    if request.headers.get("content-type") != PDF_MIME:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
    try:
        expected_size = int(request.headers["x-appsell-byte-size"])
        expected_sha256 = request.headers["x-appsell-sha256"]
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT) from error
    if not 1 <= expected_size <= MAX_PDF_BYTES or not re.fullmatch(
        r"[0-9a-f]{64}", expected_sha256
    ):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)

    job_directory = _private_job_directory("appsell-inspect-job-")
    source_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="source-", suffix=".pdf", dir=job_directory, delete=False
        ) as source:
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
        page_count = await to_thread.run_sync(
            lambda: _inspect_file(source_path, expected_size, expected_sha256, job_directory)
        )
        return JSONResponse(
            {"page_count": page_count},
            headers={
                "cache-control": "no-store, private, max-age=0",
                "pragma": "no-cache",
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
        shutil.rmtree(job_directory)


async def _links_request(request: Request) -> Response:
    if request.headers.get("content-type") != PDF_MIME:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
    try:
        expected_size = int(request.headers["x-appsell-byte-size"])
        expected_sha256 = request.headers["x-appsell-sha256"]
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT) from error
    if not 1 <= expected_size <= MAX_PDF_BYTES or not re.fullmatch(
        r"[0-9a-f]{64}", expected_sha256
    ):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)

    job_directory = _private_job_directory("appsell-links-job-")
    source_path = job_directory / "source.pdf"
    try:
        digest = hashlib.sha256()
        received = 0
        with source_path.open("xb") as source:
            os.chmod(source_path, 0o600)
            async for chunk in request.stream():
                received += len(chunk)
                if received > expected_size or received > MAX_PDF_BYTES:
                    raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
                digest.update(chunk)
                source.write(chunk)
            source.flush()
            os.fsync(source.fileno())
        if received != expected_size or digest.hexdigest() != expected_sha256:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
        manifest = await to_thread.run_sync(
            lambda: extract_pdf_link_manifest(
                source_path,
                expected_size=expected_size,
                expected_sha256_hex=expected_sha256,
            )
        )
        return JSONResponse(
            manifest,
            headers={
                "cache-control": "no-store, private, max-age=0",
                "pragma": "no-cache",
                "x-content-type-options": "nosniff",
            },
        )
    except HTTPException:
        raise
    except PdfLinksRejected as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT) from error
    finally:
        shutil.rmtree(job_directory, ignore_errors=True)


def _inspect_file(
    source: Path, expected_size: int, expected_sha256: str, job_directory: Path
) -> int:
    with (
        source.open("rb") as payload,
        verified_pdf_file(
            payload,
            expected_size=expected_size,
            expected_sha256_hex=expected_sha256,
            declared_content_type=PDF_MIME,
            temporary_directory=str(job_directory),
            isolated_service_boundary=True,
        ) as verified,
    ):
        return verified.page_count


async def _render_page_request(request: Request, page_number: int) -> Response:
    if request.headers.get("content-type") != PDF_MIME:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
    try:
        expected_size = int(request.headers["x-appsell-byte-size"])
        expected_sha256 = request.headers["x-appsell-sha256"]
        watermark = request.headers["x-appsell-watermark"]
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT) from error
    if not 1 <= expected_size <= MAX_PDF_BYTES or not re.fullmatch(
        r"[0-9a-f]{64}", expected_sha256
    ):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)

    job_directory = _private_job_directory("appsell-raster-job-")
    source_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="source-", suffix=".pdf", dir=job_directory, delete=False
        ) as source:
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
        rendered = await to_thread.run_sync(
            lambda: _render_page_file(
                source_path,
                expected_size,
                expected_sha256,
                page_number,
                watermark,
                job_directory,
            )
        )
        return Response(
            rendered.payload,
            media_type="image/png",
            headers={
                "x-appsell-page-count": str(rendered.page_count),
                "x-appsell-page-number": str(rendered.page_number),
                "x-appsell-width": str(rendered.width),
                "x-appsell-height": str(rendered.height),
                "x-appsell-sha256": rendered.sha256_hex,
                "x-appsell-source-sha256": rendered.source_sha256_hex,
                "cache-control": "no-store, private, max-age=0",
                "pragma": "no-cache",
                "x-content-type-options": "nosniff",
            },
        )
    except HTTPException:
        raise
    except PdfRasterRejected as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT) from error
    except PdfRasterUnavailable as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from error
    finally:
        if source_path is not None:
            source_path.unlink(missing_ok=True)
        shutil.rmtree(job_directory)


def _render_page_file(
    source: Path,
    expected_size: int,
    expected_sha256: str,
    page_number: int,
    watermark: str,
    job_directory: Path,
) -> RenderedPdfPage:
    with source.open("rb") as payload:
        return render_pdf_page(
            payload,
            expected_size=expected_size,
            expected_sha256_hex=expected_sha256,
            page_number=page_number,
            watermark=watermark,
            temporary_directory=str(job_directory),
        )


async def _prepare_video_request(request: Request) -> Response:
    """O vídeo do vendedor, convertido para um MP4 que o navegador toca.

    O arquivo chega inteiro pelo socket, é gravado num diretório privado do
    trabalho e some quando ele termina. O processo é reciclado depois de cada
    job de mídia, então um parser que fique num estado ruim não atende o
    próximo aluno.
    """
    try:
        expected_size = int(request.headers["x-appsell-byte-size"])
        expected_sha256 = request.headers["x-appsell-sha256"]
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT) from error
    if not 1 <= expected_size <= MAX_REMUX_BYTES or not re.fullmatch(
        r"[0-9a-f]{64}", expected_sha256
    ):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)

    job_directory = _private_job_directory("appsell-remux-job-")
    source_path: Path | None = None
    entregue = False
    try:
        with tempfile.NamedTemporaryFile(
            prefix="source-", suffix=".bin", dir=job_directory, delete=False
        ) as source:
            source_path = Path(source.name)
            digest = hashlib.sha256()
            received = 0
            async for chunk in request.stream():
                received += len(chunk)
                if received > expected_size or received > MAX_REMUX_BYTES:
                    raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
                digest.update(chunk)
                source.write(chunk)
            source.flush()
            os.fsync(source.fileno())
        os.chmod(source_path, 0o600)
        if received != expected_size or digest.hexdigest() != expected_sha256:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
        prepared = job_directory / "prepared.mp4"
        recoded = await to_thread.run_sync(lambda: remux_to_mp4(source_path, prepared))
        entregue = True

        # O arquivo sai do disco em pedaços — lê-lo inteiro seria meio giga de
        # cada lado. A limpeza fica no `finally` do próprio gerador: uma tarefa
        # de fundo só roda se a resposta terminar, e um cliente que aborta
        # deixaria o diretório para trás e o serviço drenando para sempre.
        async def _entregar() -> AsyncIterator[bytes]:
            with prepared.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    yield chunk

        def _encerrar() -> None:
            shutil.rmtree(job_directory, ignore_errors=True)
            if _recycle_enabled() and bool(getattr(request.state, "recycle_owner", False)):
                _terminate_sandbox_process()

        return _CleanupStreamingResponse(
            _entregar(),
            media_type="video/mp4",
            headers={"x-appsell-recoded": "true" if recoded else "false"},
            cleanup=_encerrar,
        )
    except HTTPException:
        raise
    except VideoRemuxRejected as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT) from error
    except VideoRemuxUnavailable as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from error
    finally:
        if source_path is not None:
            source_path.unlink(missing_ok=True)
        # Quando a resposta leva o arquivo, quem limpa é a tarefa de fundo.
        if not entregue:
            shutil.rmtree(job_directory, ignore_errors=True)


async def _probe_video_request(request: Request) -> Response:
    if request.headers.get("content-type") != VIDEO_MP4_MIME:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
    try:
        expected_size = int(request.headers["x-appsell-byte-size"])
        expected_sha256 = request.headers["x-appsell-sha256"]
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT) from error
    if not 1 <= expected_size <= MAX_VIDEO_BYTES or not re.fullmatch(
        r"[0-9a-f]{64}", expected_sha256
    ):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)

    job_directory = _private_job_directory("appsell-video-job-")
    source_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="source-", suffix=".mp4", dir=job_directory, delete=False
        ) as source:
            source_path = Path(source.name)
            digest = hashlib.sha256()
            received = 0
            async for chunk in request.stream():
                received += len(chunk)
                if received > expected_size or received > MAX_VIDEO_BYTES:
                    raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
                digest.update(chunk)
                source.write(chunk)
            source.flush()
            os.fsync(source.fileno())
        os.chmod(source_path, 0o600)
        if received != expected_size or digest.hexdigest() != expected_sha256:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
        await to_thread.run_sync(lambda: _probe_video_file(source_path, received))
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except HTTPException:
        raise
    except VideoRejected as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT) from error
    except VideoProbeUnavailable as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from error
    finally:
        if source_path is not None:
            source_path.unlink(missing_ok=True)
        shutil.rmtree(job_directory)


def _private_job_directory(prefix: str) -> Path:
    path = Path(tempfile.mkdtemp(prefix=prefix))
    os.chmod(path, 0o700)
    return path


def _probe_video_file(source: Path, expected_size: int) -> None:
    _validate_mp4_container(source, expected_size)
    probe_mp4_file(source)
