from __future__ import annotations

import asyncio
import hashlib
import io
import shutil
import struct
from pathlib import Path

import httpx
import pytest
from fastapi import Response
from PIL import Image
from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]

from appsell_api import pdf_sandbox_service
from appsell_api.pdf_raster import PdfRasterRejected, normalize_watermark
from appsell_api.pdf_sandbox_service import app
from appsell_api.video_remux import MAX_DERIVATIVE_BYTES as MAX_REMUX_BYTES
from appsell_api.video_verifier import MAX_VIDEO_BYTES, VideoProbeUnavailable, VideoRejected


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
async def test_isolated_inspector_returns_only_validated_page_count() -> None:
    payload = valid_pdf(page_count=3)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        response = await client.post(
            "/inspect",
            content=payload,
            headers={
                "content-type": "application/pdf",
                "x-appsell-byte-size": str(len(payload)),
                "x-appsell-sha256": hashlib.sha256(payload).hexdigest(),
            },
        )

    assert response.status_code == 200
    assert response.json() == {"page_count": 3}
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store, private, max-age=0"
    assert not response.content.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_isolated_link_inspector_returns_a_bounded_passive_manifest() -> None:
    payload = valid_pdf(page_count=2)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        response = await client.post(
            "/links",
            content=payload,
            headers={
                "content-type": "application/pdf",
                "x-appsell-byte-size": str(len(payload)),
                "x-appsell-sha256": hashlib.sha256(payload).hexdigest(),
            },
        )

    assert response.status_code == 200
    assert response.json() == {"schema_version": 1, "page_count": 2, "links": []}
    assert response.headers["cache-control"] == "no-store, private, max-age=0"
    assert len(response.content) < 256 * 1024


@pytest.mark.asyncio
async def test_isolated_parser_and_inspector_share_one_serial_media_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    starts = 0

    async def slow_normalize(_request: object) -> Response:
        nonlocal starts
        starts += 1
        started.set()
        await release.wait()
        return Response(b"ok", status_code=200)

    monkeypatch.setattr(pdf_sandbox_service, "_normalize_request", slow_normalize)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        first = asyncio.create_task(client.post("/normalize"))
        await asyncio.wait_for(started.wait(), timeout=1)
        overloaded = await client.post("/inspect")
        release.set()
        await first

    assert starts == 1
    assert overloaded.status_code == 429
    assert overloaded.headers["retry-after"] == "2"


@pytest.mark.asyncio
async def test_isolated_renderer_returns_bounded_watermarked_png() -> None:
    payload = valid_pdf(page_count=2)
    source_sha256 = hashlib.sha256(payload).hexdigest()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        response = await client.post(
            "/render-page/2",
            content=payload,
            headers={
                "content-type": "application/pdf",
                "x-appsell-byte-size": str(len(payload)),
                "x-appsell-sha256": source_sha256,
                "x-appsell-watermark": "user.demo@autonomia.site - tenant 4df98a2e",
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store, private, max-age=0"
    assert response.headers["x-appsell-page-count"] == "2"
    assert response.headers["x-appsell-page-number"] == "2"
    assert response.headers["x-appsell-source-sha256"] == source_sha256
    assert response.headers["x-appsell-sha256"] == hashlib.sha256(response.content).hexdigest()
    assert len(response.content) <= 8 * 1024 * 1024
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.format == "PNG"
        assert max(image.size) <= 1_600
        assert image.mode == "RGB"


@pytest.mark.asyncio
async def test_isolated_renderer_changes_png_for_each_watermark() -> None:
    payload = valid_pdf()
    headers = {
        "content-type": "application/pdf",
        "x-appsell-byte-size": str(len(payload)),
        "x-appsell-sha256": hashlib.sha256(payload).hexdigest(),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        first = await client.post(
            "/render-page/1",
            content=payload,
            headers={**headers, "x-appsell-watermark": "buyer-one@example.test"},
        )
        second = await client.post(
            "/render-page/1",
            content=payload,
            headers={**headers, "x-appsell-watermark": "buyer-two@example.test"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.content != second.content
    assert first.headers["x-appsell-sha256"] != second.headers["x-appsell-sha256"]


@pytest.mark.asyncio
async def test_isolated_renderer_rejects_page_past_document_end() -> None:
    payload = valid_pdf()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        response = await client.post(
            "/render-page/2",
            content=payload,
            headers={
                "content-type": "application/pdf",
                "x-appsell-byte-size": str(len(payload)),
                "x-appsell-sha256": hashlib.sha256(payload).hexdigest(),
                "x-appsell-watermark": "buyer@example.test",
            },
        )

    assert response.status_code == 422


def test_watermark_normalizer_rejects_invisible_formatting() -> None:
    assert normalize_watermark("  buyer@example.test   tenant-1 ") == (
        "buyer@example.test tenant-1"
    )
    with pytest.raises(PdfRasterRejected, match="unsafe"):
        normalize_watermark("buyer@example.test\u202etenant-1")


@pytest.mark.asyncio
async def test_isolated_renderer_rejects_overload_without_starting_another_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    starts = 0

    async def slow_render(_request: object, _page_number: int) -> Response:
        nonlocal starts
        starts += 1
        started.set()
        await release.wait()
        return Response(b"ok", status_code=200)

    monkeypatch.setattr(pdf_sandbox_service, "_render_page_request", slow_render)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        first = asyncio.create_task(client.post("/render-page/1"))
        await asyncio.wait_for(started.wait(), timeout=1)
        overloaded = await client.post("/render-page/1")
        release.set()
        await first

    assert starts == 1
    assert overloaded.status_code == 429
    assert overloaded.headers["retry-after"] == "2"


@pytest.mark.asyncio
async def test_media_response_recycles_the_entire_sandbox_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recycled = 0

    def recycle() -> None:
        nonlocal recycled
        recycled += 1

    monkeypatch.setenv("APPSELL_PDF_SANDBOX_RECYCLE_AFTER_JOB", "true")
    monkeypatch.setattr(pdf_sandbox_service, "_terminate_sandbox_process", recycle)
    pdf_sandbox_service._DRAINING.clear()
    payload = valid_pdf()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
        ) as client:
            response = await client.post(
                "/inspect",
                content=payload,
                headers={
                    "content-type": "application/pdf",
                    "x-appsell-byte-size": str(len(payload)),
                    "x-appsell-sha256": hashlib.sha256(payload).hexdigest(),
                },
            )
            draining = await client.post(
                "/inspect",
                content=payload,
                headers={
                    "content-type": "application/pdf",
                    "x-appsell-byte-size": str(len(payload)),
                    "x-appsell-sha256": hashlib.sha256(payload).hexdigest(),
                },
            )
    finally:
        pdf_sandbox_service._DRAINING.clear()

    assert response.status_code == 200
    assert response.headers["x-appsell-worker-recycle"] == "1"
    assert draining.status_code == 503
    assert draining.headers["retry-after"] == "1"
    assert "x-appsell-worker-recycle" not in draining.headers
    assert recycled == 1


@pytest.mark.asyncio
async def test_queued_request_rechecks_draining_after_acquiring_media_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def slow_inspect(_request: object) -> Response:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return Response(b"ok", status_code=200)

    monkeypatch.setenv("APPSELL_PDF_SANDBOX_RECYCLE_AFTER_JOB", "true")
    monkeypatch.setattr(pdf_sandbox_service, "_terminate_sandbox_process", lambda: None)
    monkeypatch.setattr(pdf_sandbox_service, "_inspect_request", slow_inspect)
    # Force the scheduling race where two requests pass the optimistic pre-check.
    monkeypatch.setattr(pdf_sandbox_service, "_reject_busy_or_draining", lambda: None)
    pdf_sandbox_service._DRAINING.clear()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
        ) as client:
            first = asyncio.create_task(client.post("/inspect"))
            await asyncio.wait_for(started.wait(), timeout=1)
            queued = asyncio.create_task(client.post("/inspect"))
            await asyncio.sleep(0)
            release.set()
            first_response, queued_response = await asyncio.gather(first, queued)
    finally:
        pdf_sandbox_service._DRAINING.clear()

    assert first_response.status_code == 200
    assert queued_response.status_code == 503
    assert "x-appsell-worker-recycle" not in queued_response.headers
    assert calls == 1


@pytest.mark.asyncio
async def test_health_requires_pdftoppm(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_pdftoppm(binary: str) -> str | None:
        return None if binary == "pdftoppm" else f"/usr/bin/{binary}"

    monkeypatch.setattr(shutil, "which", missing_pdftoppm)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_isolated_service_probes_bounded_video_without_returning_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = valid_mp4()
    probed: list[bytes] = []

    def record_probe(path: Path) -> None:
        probed.append(path.read_bytes())

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
        raise VideoProbeUnavailable("missing ffprobe")

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


@pytest.mark.asyncio
async def test_o_modo_catalogo_recusa_recodificar_e_confere_a_saida(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O catálogo troca o invólucro e confere o que sai, não o que entra.

    Recodificar estoura o lease de 600 s do worker. E a garantia que interessa
    é sobre o arquivo que o aluno recebe, que é o derivado.
    """
    payload = valid_mp4()
    preparado = b"MP4-PREPARADO"
    pedido: dict[str, object] = {}
    conferidos: list[bytes] = []

    def falso_remux(source: Path, destination: Path, *, allow_transcode: bool = True) -> bool:
        pedido["allow_transcode"] = allow_transcode
        pedido["origem"] = source.read_bytes()
        destination.write_bytes(preparado)
        return False

    monkeypatch.setattr(pdf_sandbox_service, "remux_to_mp4", falso_remux)
    monkeypatch.setattr(
        pdf_sandbox_service,
        "_probe_video_file",
        lambda caminho, _tamanho: conferidos.append(caminho.read_bytes()),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        response = await client.post(
            "/prepare-video",
            content=payload,
            headers={
                "x-appsell-byte-size": str(len(payload)),
                "x-appsell-sha256": hashlib.sha256(payload).hexdigest(),
                "x-appsell-mode": "catalogue",
            },
        )
        corpo = response.read()

    assert response.status_code == 200
    assert corpo == preparado
    assert pedido["allow_transcode"] is False
    assert pedido["origem"] == payload
    assert conferidos == [preparado]


@pytest.mark.asyncio
async def test_o_video_do_pdf_continua_podendo_recodificar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sem o cabeçalho, nada muda para o caminho que já estava em produção."""
    payload = valid_mp4()
    pedido: dict[str, object] = {}
    conferidos: list[bytes] = []

    def falso_remux(source: Path, destination: Path, *, allow_transcode: bool = True) -> bool:
        pedido["allow_transcode"] = allow_transcode
        destination.write_bytes(b"MP4")
        return True

    monkeypatch.setattr(pdf_sandbox_service, "remux_to_mp4", falso_remux)
    monkeypatch.setattr(
        pdf_sandbox_service,
        "_probe_video_file",
        lambda caminho, _tamanho: conferidos.append(caminho.read_bytes()),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        response = await client.post(
            "/prepare-video",
            content=payload,
            headers={
                "x-appsell-byte-size": str(len(payload)),
                "x-appsell-sha256": hashlib.sha256(payload).hexdigest(),
            },
        )
        response.read()

    assert response.status_code == 200
    assert response.headers["x-appsell-recoded"] == "true"
    assert pedido["allow_transcode"] is True
    # A conferência do catálogo é do catálogo: o vídeo do PDF aceita o que o
    # navegador toca, que é mais largo do que o material de aula aceita.
    assert conferidos == []


@pytest.mark.asyncio
async def test_o_modo_catalogo_recusa_o_derivado_que_nao_passa_na_conferencia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = valid_mp4()

    def falso_remux(source: Path, destination: Path, *, allow_transcode: bool = True) -> bool:
        destination.write_bytes(b"MP4")
        return False

    def recusa(_caminho: Path, _tamanho: int) -> None:
        raise VideoRejected("audio codec must be AAC")

    monkeypatch.setattr(pdf_sandbox_service, "remux_to_mp4", falso_remux)
    monkeypatch.setattr(pdf_sandbox_service, "_probe_video_file", recusa)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        response = await client.post(
            "/prepare-video",
            content=payload,
            headers={
                "x-appsell-byte-size": str(len(payload)),
                "x-appsell-sha256": hashlib.sha256(payload).hexdigest(),
                "x-appsell-mode": "catalogue",
            },
        )

    assert response.status_code == 422


def test_cada_modo_carrega_o_proprio_teto() -> None:
    """O catálogo é enviado pelo vendedor; o vídeo do PDF é baixado por nós."""
    assert pdf_sandbox_service._prepare_ceiling("catalogue") == MAX_VIDEO_BYTES
    assert pdf_sandbox_service._prepare_ceiling(None) == MAX_REMUX_BYTES
    assert pdf_sandbox_service._prepare_ceiling("qualquer-outra-coisa") == MAX_REMUX_BYTES


def valid_pdf(*, page_count: int = 1) -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(320, 480), invariant=1)
    for page_number in range(1, page_count + 1):
        canvas.drawString(32, 430, f"Isolated parser contract page {page_number}")
        canvas.showPage()
    canvas.save()
    return output.getvalue()


def valid_mp4() -> bytes:
    def box(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I4s", len(payload) + 8, kind) + payload

    return (
        box(b"ftyp", b"isom\0\0\0\0isommp42") + box(b"moov", b"metadata") + box(b"mdat", b"video")
    )
