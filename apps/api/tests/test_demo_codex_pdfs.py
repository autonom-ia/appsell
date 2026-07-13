from collections.abc import Callable
from pathlib import Path
from runpy import run_path
from types import SimpleNamespace
from typing import Any

import pytest
from pypdf import PdfReader
from pypdf.generic import ContentStream
from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]


class RecordingCanvas:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Callable[..., None]:
        def record(*arguments: Any, **keywords: Any) -> None:
            self.calls.append((name, arguments, keywords))

        return record


def test_pdf_page_chrome_paints_an_opaque_white_background_first() -> None:
    root = Path(__file__).resolve().parents[3]
    namespace = run_path(str(root / "scripts" / "generate-demo-codex-pdfs.py"))
    canvas = RecordingCanvas()

    namespace["page_chrome"](canvas, SimpleNamespace(page=1))

    assert canvas.calls[:3] == [
        ("saveState", (), {}),
        ("setFillColor", (colors.white,), {}),
        ("rect", (0, 0, A4[0], A4[1]), {"stroke": 0, "fill": 1}),
    ]


def test_committed_pdfs_start_with_a_full_page_white_paint() -> None:
    root = Path(__file__).resolve().parents[3]
    for path in sorted((root / "output" / "pdf" / "codex").glob("*.pdf")):
        reader = PdfReader(path)
        assert len(reader.pages) == 1, path.name
        page = reader.pages[0]
        assert float(page.mediabox.width) == pytest.approx(A4[0], abs=0.001), path.name
        assert float(page.mediabox.height) == pytest.approx(A4[1], abs=0.001), path.name

        operations = ContentStream(page["/Contents"], reader).operations
        assert [(operands, operator) for operands, operator in operations[5:8]] == [
            ([], b"q"),
            ([1, 1, 1], b"rg"),
            ([], b"n"),
        ], path.name
        rectangle, rectangle_operator = operations[8]
        assert rectangle_operator == b"re", path.name
        assert [float(value) for value in rectangle] == pytest.approx(
            [0, 0, A4[0], A4[1]], abs=0.001
        ), path.name
        assert operations[9] == ([], b"f*"), path.name
