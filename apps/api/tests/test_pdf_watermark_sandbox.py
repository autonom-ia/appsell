"""Rota `/watermark` do sandbox de PDF (#559): contrato de entrada e de saída."""

from __future__ import annotations

import base64
import hashlib
import io

import httpx
import pikepdf
import pytest
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]

from appsell_api.pdf_sandbox_service import app

LABEL = "Licenciado para ab***@example.test • Compra K7QM-3XRP-T9WD"


def make_pdf(page_count: int = 2) -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(320, 480), invariant=1)
    for number in range(1, page_count + 1):
        canvas.drawString(32, 430, f"Material {number}")
        canvas.showPage()
    canvas.save()
    return output.getvalue()


def encoded(label: str) -> str:
    return base64.urlsafe_b64encode(label.encode("utf-8")).decode("ascii").rstrip("=")


def headers_for(payload: bytes, label: str = LABEL) -> dict[str, str]:
    return {
        "content-type": "application/pdf",
        "x-appsell-byte-size": str(len(payload)),
        "x-appsell-sha256": hashlib.sha256(payload).hexdigest(),
        "x-appsell-watermark-b64": encoded(label),
    }


async def post(payload: bytes, headers: dict[str, str]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://parser.test"
    ) as client:
        return await client.post("/watermark", content=payload, headers=headers)


@pytest.mark.asyncio
async def test_sandbox_returns_the_stamped_copy() -> None:
    payload = make_pdf()
    response = await post(payload, headers_for(payload))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store, private, max-age=0"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-appsell-page-count"] == "2"
    assert response.headers["x-appsell-source-sha256"] == hashlib.sha256(payload).hexdigest()
    assert response.headers["x-appsell-sha256"] == hashlib.sha256(response.content).hexdigest()
    assert response.headers["x-appsell-byte-size"] == str(len(response.content))
    pages = PdfReader(io.BytesIO(response.content)).pages
    assert all(LABEL in " ".join((page.extract_text() or "").split()) for page in pages)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header,value",
    [
        ("x-appsell-watermark-b64", "nao e base64!"),
        ("x-appsell-watermark-b64", "TGljZW5j*aWFkbw"),
        ("x-appsell-watermark-b64", encoded("Licenciado para 李***@x.test")),
        ("x-appsell-watermark-b64", ""),
        ("x-appsell-sha256", "z" * 64),
        ("x-appsell-sha256", "a" * 63),
        ("x-appsell-byte-size", "abc"),
        ("x-appsell-byte-size", "0"),
        ("x-appsell-byte-size", "-5"),
        ("x-appsell-byte-size", str(10**12)),
        ("x-appsell-watermark-b64", "A" * 1_201),
        ("x-appsell-watermark-b64", encoded(LABEL).replace("-", "+") + "+/"),
        ("x-appsell-watermark-b64", encoded(LABEL) + "=="),
    ],
)
async def test_sandbox_refuses_input_outside_the_contract(header: str, value: str) -> None:
    payload = make_pdf()
    response = await post(payload, {**headers_for(payload), header: value})

    assert response.status_code == 422
    assert not response.content.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_sandbox_answers_503_when_the_normalizer_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from appsell_api import pdf_sandbox_service
    from appsell_api.pdf_verifier import PdfSandboxUnavailable

    def unavailable(*args: object, **kwargs: object) -> object:
        raise PdfSandboxUnavailable("normalizer is down")

    monkeypatch.setattr(pdf_sandbox_service, "stamp_pdf", unavailable)
    payload = make_pdf()
    response = await post(payload, headers_for(payload))

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_sandbox_refuses_missing_label() -> None:
    payload = make_pdf()
    headers = headers_for(payload)
    del headers["x-appsell-watermark-b64"]
    response = await post(payload, headers)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_sandbox_refuses_wrong_media_type() -> None:
    payload = make_pdf()
    response = await post(payload, {**headers_for(payload), "content-type": "text/plain"})

    assert response.status_code == 415


@pytest.mark.asyncio
async def test_sandbox_refuses_body_that_does_not_match_the_hash() -> None:
    payload = make_pdf()
    other = make_pdf(page_count=3)
    response = await post(other, {**headers_for(payload), "x-appsell-byte-size": str(len(other))})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_sandbox_refuses_body_larger_than_declared() -> None:
    payload = make_pdf()
    response = await post(payload, {**headers_for(payload), "x-appsell-byte-size": "10"})

    assert response.status_code == 413
    assert not response.content.startswith(b"%PDF-")


def hostile_pdf(attach: str) -> bytes:
    with pikepdf.open(io.BytesIO(make_pdf())) as pdf:
        action = pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS=pikepdf.String("app.alert(1)"))
        if attach == "OpenAction":
            pdf.Root[pikepdf.Name.OpenAction] = pdf.make_indirect(action)
        elif attach == "AA":
            pdf.pages[0].obj[pikepdf.Name.AA] = pikepdf.Dictionary(O=pdf.make_indirect(action))
        elif attach == "JavaScript":
            pdf.Root[pikepdf.Name.Names] = pikepdf.Dictionary(
                JavaScript=pikepdf.Dictionary(
                    Names=pikepdf.Array([pikepdf.String("x"), pdf.make_indirect(action)])
                )
            )
        elif attach == "EmbeddedFiles":
            embedded = pikepdf.Stream(pdf, b"MZ executable")
            embedded[pikepdf.Name.Type] = pikepdf.Name.EmbeddedFile
            spec = pikepdf.Dictionary(
                Type=pikepdf.Name.Filespec,
                F=pikepdf.String("a.exe"),
                EF=pikepdf.Dictionary(F=pdf.make_indirect(embedded)),
            )
            pdf.Root[pikepdf.Name.Names] = pikepdf.Dictionary(
                EmbeddedFiles=pikepdf.Dictionary(
                    Names=pikepdf.Array([pikepdf.String("a.exe"), pdf.make_indirect(spec)])
                )
            )
        output = io.BytesIO()
        pdf.save(output)
        return output.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("attach", ["OpenAction", "AA", "JavaScript", "EmbeddedFiles"])
async def test_sandbox_refuses_pdf_with_active_content(attach: str) -> None:
    payload = hostile_pdf(attach)
    response = await post(payload, headers_for(payload))

    assert response.status_code == 422
    assert not response.content.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_sandbox_refuses_invalid_rotate_with_422() -> None:
    with pikepdf.open(io.BytesIO(make_pdf())) as pdf:
        pdf.pages[0].obj[pikepdf.Name.Rotate] = pikepdf.Name("/Noventa")
        output = io.BytesIO()
        pdf.save(output)
    payload = output.getvalue()
    response = await post(payload, headers_for(payload))

    assert response.status_code == 422
