"""Cópia marcada do PDF baixado (#559): o carimbo, sem rota nem banco.

O que estes testes guardam:
- cada página sai com a linha de licença, legível por extração de texto;
- a página girada recebe a linha na borda de baixo de quem lê, não de lado;
- texto hostil vira texto, nunca operador do PDF;
- o que não cabe no conjunto de caracteres da fonte é recusado, não trocado;
- entrada fora do contrato (tamanho, hash, PDF inválido) é recusada.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
from pathlib import Path

import pikepdf
import pytest
from PIL import Image
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]

from appsell_api.pdf_watermark import PdfWatermarkRejected, stamp_pdf

LABEL = "Licenciado para ab***@example.test • Compra K7QM-3XRP-T9WD"


def make_pdf(*, page_count: int = 1, size: tuple[float, float] = (320, 480)) -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=size, invariant=1)
    for page_number in range(1, page_count + 1):
        canvas.drawString(32, size[1] - 50, f"Material de apoio, página {page_number}")
        canvas.showPage()
    canvas.save()
    return output.getvalue()


def rotated(payload: bytes, degrees: int) -> bytes:
    with pikepdf.open(io.BytesIO(payload)) as pdf:
        for page in pdf.pages:
            page.obj[pikepdf.Name.Rotate] = degrees
        output = io.BytesIO()
        pdf.save(output)
        return output.getvalue()


def stamp(payload: bytes, label: str = LABEL, tmp_path: Path | None = None):  # type: ignore[no-untyped-def]
    return stamp_pdf(
        io.BytesIO(payload),
        expected_size=len(payload),
        expected_sha256_hex=hashlib.sha256(payload).hexdigest(),
        watermark=label,
        temporary_directory=str(tmp_path) if tmp_path else None,
    )


def page_texts(payload: bytes) -> list[str]:
    pages = PdfReader(io.BytesIO(payload)).pages
    return [" ".join((page.extract_text() or "").split()) for page in pages]


def test_every_page_carries_the_license_line() -> None:
    result = stamp(make_pdf(page_count=3))

    assert result.page_count == 3
    texts = page_texts(result.payload)
    assert len(texts) == 3
    for number, text in enumerate(texts, start=1):
        assert LABEL in text
        assert f"Material de apoio, página {number}" in text


def test_result_describes_itself() -> None:
    source = make_pdf()
    result = stamp(source)

    assert result.payload.startswith(b"%PDF-")
    assert result.byte_size == len(result.payload)
    assert result.sha256_hex == hashlib.sha256(result.payload).hexdigest()
    assert result.source_sha256_hex == hashlib.sha256(source).hexdigest()
    assert result.payload != source


def test_two_buyers_get_two_different_copies() -> None:
    source = make_pdf()
    first = stamp(source, "Licenciado para ab***@example.test • Compra AAAA-BBBB-CCCC")
    second = stamp(source, "Licenciado para cd***@example.test • Compra DDDD-EEEE-FFFF")

    assert first.sha256_hex != second.sha256_hex
    assert "AAAA-BBBB-CCCC" in page_texts(first.payload)[0]
    assert "DDDD-EEEE-FFFF" in page_texts(second.payload)[0]
    assert "AAAA-BBBB-CCCC" not in page_texts(second.payload)[0]


def test_hostile_label_stays_text() -> None:
    # Parênteses e barra invertida são os delimitadores de string do PDF: se
    # vazassem, o resto da linha viraria operador.
    label = r"Licenciado para a)\***@x.test ET Q (Tj) \) • Compra Q"
    result = stamp(make_pdf(), label)

    assert label in page_texts(result.payload)[0]
    with pikepdf.open(io.BytesIO(result.payload)) as pdf:
        assert len(pdf.pages) == 1
        operators = [str(item.operator) for item in pikepdf.parse_content_stream(pdf.pages[0])]
    assert operators == [
        "q",
        "Do",
        "Q",
        "q",
        "cm",
        "rg",
        "BT",
        "Tf",
        "Td",
        "Tj",
        "ET",
        "Q",
    ]


def test_label_outside_the_font_charset_is_refused() -> None:
    with pytest.raises(PdfWatermarkRejected):
        stamp(make_pdf(), "Licenciado para 李***@example.test • Compra X")


@pytest.mark.parametrize("label", ["", "   ", "x" * 201, "Licenciado\x00para"])
def test_label_outside_the_contract_is_refused(label: str) -> None:
    with pytest.raises(PdfWatermarkRejected):
        stamp(make_pdf(), label)


def test_size_mismatch_is_refused() -> None:
    payload = make_pdf()
    with pytest.raises(PdfWatermarkRejected):
        stamp_pdf(
            io.BytesIO(payload),
            expected_size=len(payload) - 1,
            expected_sha256_hex=hashlib.sha256(payload).hexdigest(),
            watermark=LABEL,
        )


def test_hash_mismatch_is_refused() -> None:
    payload = make_pdf()
    with pytest.raises(PdfWatermarkRejected):
        stamp_pdf(
            io.BytesIO(payload),
            expected_size=len(payload),
            expected_sha256_hex="0" * 64,
            watermark=LABEL,
        )


def test_not_a_pdf_is_refused() -> None:
    payload = b"%PDF-1.7\nnot really a pdf\n%%EOF\n"
    with pytest.raises(PdfWatermarkRejected):
        stamp(payload)


def dark_rows(png: bytes) -> list[int]:
    with Image.open(io.BytesIO(png)) as image:
        gray = image.convert("L")
        width, height = gray.size
        pixels = gray.load()
        assert pixels is not None
        return [
            y
            for y in range(height)
            if any(int(pixels[x, y]) < 200 for x in range(width))  # type: ignore[arg-type]
        ]


@pytest.mark.parametrize("degrees", [0, 90, 180, 270])
def test_line_sits_at_the_reader_bottom_on_rotated_pages(degrees: int, tmp_path: Path) -> None:
    # Página só com o carimbo: tudo o que é escuro na imagem é a linha.
    blank = io.BytesIO()
    canvas = Canvas(blank, pagesize=(320, 480), invariant=1)
    canvas.showPage()
    canvas.save()
    result = stamp(rotated(blank.getvalue(), degrees))
    stamped = tmp_path / "stamped.pdf"
    stamped.write_bytes(result.payload)

    pdftoppm = shutil.which("pdftoppm")
    assert pdftoppm is not None
    subprocess.run(  # noqa: S603 - binário resolvido e argumentos fixos do teste
        [pdftoppm, "-png", "-r", "36", "-singlefile", str(stamped), str(tmp_path / "page")],
        check=True,
        timeout=30,
    )
    png = (tmp_path / "page.png").read_bytes()
    with Image.open(io.BytesIO(png)) as image:
        width, height = image.size
    rows = dark_rows(png)

    assert rows, "a linha de licença não aparece na página renderizada"
    assert min(rows) > height * 0.85, "a linha precisa ficar no rodapé de quem lê"
    if degrees in (90, 270):
        assert width > height


def render_png(payload: bytes, tmp_path: Path) -> bytes:
    pdftoppm = shutil.which("pdftoppm")
    assert pdftoppm is not None, "pdftoppm é obrigatório para provar a renderização"
    stamped = tmp_path / "stamped.pdf"
    stamped.write_bytes(payload)
    subprocess.run(  # noqa: S603 - binário resolvido e argumentos fixos do teste
        [pdftoppm, "-png", "-r", "36", "-singlefile", str(stamped), str(tmp_path / "page")],
        check=True,
        timeout=30,
    )
    return (tmp_path / "page.png").read_bytes()


def with_raw_content(content: bytes) -> bytes:
    blank = io.BytesIO()
    canvas = Canvas(blank, pagesize=(320, 480), invariant=1)
    canvas.showPage()
    canvas.save()
    with pikepdf.open(io.BytesIO(blank.getvalue())) as pdf:
        pdf.pages[0].obj[pikepdf.Name.Contents] = pikepdf.Stream(pdf, content)
        output = io.BytesIO()
        pdf.save(output)
        return output.getvalue()


def test_line_survives_content_that_leaves_the_graphics_state_unbalanced(
    tmp_path: Path,
) -> None:
    # Dois `q` sem `Q`, uma escala que encolhe tudo a 1% e texto invisível:
    # embrulhar o original em q/Q não bastaria.
    hostile = with_raw_content(b"q q 0.01 0 0 0.01 0 0 cm 3 Tr 1 1 1 rg")
    result = stamp(hostile)

    png = render_png(result.payload, tmp_path)
    with Image.open(io.BytesIO(png)) as image:
        height = image.size[1]
    rows = dark_rows(png)
    assert rows, "a linha de licença sumiu com o estado gráfico do original"
    assert min(rows) > height * 0.85
    assert LABEL in page_texts(result.payload)[0]


def test_original_text_is_still_on_the_page() -> None:
    result = stamp(make_pdf(page_count=2))
    with pikepdf.open(io.BytesIO(result.payload)) as pdf:
        for page in pdf.pages:
            assert isinstance(page.obj.Contents, pikepdf.Array)
    assert "Material de apoio, página 2" in page_texts(result.payload)[1]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("/Rotate", pikepdf.Name("/Noventa")),
        ("/Rotate", pikepdf.String("90")),
        ("/CropBox", pikepdf.Array([pikepdf.Name("/a"), 0, 320, 480])),
        ("/CropBox", pikepdf.Array([0, 0, 0, 0])),
    ],
)
def test_invalid_page_geometry_is_refused_not_a_crash(key: str, value: object) -> None:
    with pikepdf.open(io.BytesIO(make_pdf())) as pdf:
        pdf.pages[0].obj[pikepdf.Name(key)] = value
        output = io.BytesIO()
        pdf.save(output)
    with pytest.raises(PdfWatermarkRejected):
        stamp(output.getvalue())


def test_too_many_pages_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from appsell_api import pdf_watermark

    monkeypatch.setattr(pdf_watermark, "MAX_STAMP_PAGES", 2)
    with pytest.raises(PdfWatermarkRejected):
        stamp(make_pdf(page_count=3))
