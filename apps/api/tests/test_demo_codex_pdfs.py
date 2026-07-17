import hashlib
import io
import json
import zlib
from collections.abc import Callable
from pathlib import Path
from runpy import run_path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo
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


def canonical_test_png(color: tuple[int, int, int] = (255, 255, 255)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (4, 3), color).save(output, format="PNG", optimize=False, compress_level=6)
    return output.getvalue()


def page_entry(payload: bytes, *, slug: str = "lesson") -> dict[str, object]:
    return {
        "page_number": 1,
        "storage_driver": "legacy_bundle",
        "storage_bucket": "codex-demo",
        "object_key": f"{slug}/pages/1.png",
        "storage_version_id": None,
        "storage_etag": None,
        "byte_size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "pixel_width": 4,
        "pixel_height": 3,
    }


def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(data, zlib.crc32(chunk_type)) & 0xFFFFFFFF
    return len(data).to_bytes(4, "big") + chunk_type + data + crc.to_bytes(4, "big")


def replace_png_chunk(payload: bytes, chunk_type: bytes, replacement: bytes) -> bytes:
    type_offset = payload.index(chunk_type)
    length = int.from_bytes(payload[type_offset - 4 : type_offset], "big")
    chunk_start = type_offset - 4
    chunk_end = type_offset + 4 + length + 4
    return payload[:chunk_start] + replacement + payload[chunk_end:]


def load_generator(tmp_path: Path) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[3]
    namespace = run_path(str(root / "scripts" / "generate-demo-codex-pdfs.py"))
    namespace["_committed_pages"].__globals__["OUTPUT"] = tmp_path
    return namespace


def test_committed_demo_pages_are_reused_without_platform_dependent_rasterization(
    tmp_path: Path,
) -> None:
    namespace = load_generator(tmp_path)
    slug = "lesson"
    page = canonical_test_png()
    page_path = tmp_path / slug / "pages" / "1.png"
    page_path.parent.mkdir(parents=True)
    page_path.write_bytes(page)

    pages = namespace["_committed_pages"](
        slug=slug,
        source_sha256="a" * 64,
        previous={
            "sha256": "a" * 64,
            "raster": {"pages": [page_entry(page)]},
        },
    )

    assert pages == [page_entry(page)]


def test_committed_demo_pages_require_explicit_refresh_when_source_changes() -> None:
    root = Path(__file__).resolve().parents[3]
    namespace = run_path(str(root / "scripts" / "generate-demo-codex-pdfs.py"))

    with pytest.raises(RuntimeError, match="--refresh-pages"):
        namespace["_committed_pages"](
            slug="lesson",
            source_sha256="b" * 64,
            previous={"sha256": "a" * 64, "raster": {"pages": []}},
        )


def test_default_generation_does_not_overwrite_pdf_before_drift_validation(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[3]
    namespace = run_path(str(root / "scripts" / "generate-demo-codex-pdfs.py"))
    globals_ = namespace["main"].__globals__
    output = tmp_path / "output"
    output.mkdir()
    lesson = namespace["LESSONS"][0]
    pdf = output / f"{lesson['slug']}.pdf"
    pdf.write_bytes(b"approved-pdf")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "slug": lesson["slug"],
                    "sha256": "0" * 64,
                    "raster": {"pages": []},
                }
            ]
        ),
        encoding="utf-8",
    )
    globals_.update(
        {
            "OUTPUT": output,
            "MANIFEST": manifest,
            "SEED_MANIFEST": tmp_path / "seed.json",
            "LESSONS": [lesson],
        }
    )

    with pytest.raises(RuntimeError, match="--refresh-pages"):
        namespace["main"]()

    assert pdf.read_bytes() == b"approved-pdf"


def test_refresh_failure_preserves_previous_page_tree(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3]
    namespace = run_path(str(root / "scripts" / "generate-demo-codex-pdfs.py"))
    globals_ = namespace["main"].__globals__
    output = tmp_path / "output"
    lesson = namespace["LESSONS"][0]
    old_page = output / str(lesson["slug"]) / "pages" / "1.png"
    old_page.parent.mkdir(parents=True)
    old_page.write_bytes(b"approved-page")

    def fail_raster(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected raster failure")

    globals_.update(
        {
            "OUTPUT": output,
            "MANIFEST": tmp_path / "missing-manifest.json",
            "SEED_MANIFEST": tmp_path / "seed.json",
            "LESSONS": [lesson],
            "build_pdf_page_archive": fail_raster,
        }
    )

    with pytest.raises(RuntimeError, match="injected raster failure"):
        namespace["main"](refresh_pages=True)

    assert old_page.read_bytes() == b"approved-page"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("page_number", 2),
        ("storage_driver", "other"),
        ("storage_bucket", "other"),
        ("object_key", "other/pages/1.png"),
        ("storage_version_id", "v1"),
        ("storage_etag", "etag"),
        ("byte_size", 1),
        ("sha256", "0" * 64),
        ("pixel_width", 5),
        ("pixel_height", 4),
    ],
)
def test_committed_demo_pages_reject_all_manifest_metadata_drift(
    tmp_path: Path, field: str, value: object
) -> None:
    namespace = load_generator(tmp_path)
    page = canonical_test_png()
    page_path = tmp_path / "lesson" / "pages" / "1.png"
    page_path.parent.mkdir(parents=True)
    page_path.write_bytes(page)
    approved = page_entry(page)
    approved[field] = value

    with pytest.raises(RuntimeError, match="--refresh-pages"):
        namespace["_committed_pages"](
            slug="lesson",
            source_sha256="a" * 64,
            previous={"sha256": "a" * 64, "raster": {"pages": [approved]}},
        )


def test_committed_demo_pages_reject_gaps_extras_and_symlinks(tmp_path: Path) -> None:
    namespace = load_generator(tmp_path)
    pages_root = tmp_path / "lesson" / "pages"
    pages_root.mkdir(parents=True)
    page = canonical_test_png()
    (pages_root / "1.png").write_bytes(page)
    (pages_root / "3.png").write_bytes(page)

    with pytest.raises(RuntimeError, match="incomplete"):
        namespace["_committed_pages"](
            slug="lesson",
            source_sha256="a" * 64,
            previous={
                "sha256": "a" * 64,
                "raster": {"pages": [page_entry(page), page_entry(page)]},
            },
        )

    (pages_root / "3.png").unlink()
    target = tmp_path / "target.png"
    target.write_bytes(page)
    (pages_root / "1.png").unlink()
    (pages_root / "1.png").symlink_to(target)
    with pytest.raises(RuntimeError, match="incomplete"):
        namespace["_committed_pages"](
            slug="lesson",
            source_sha256="a" * 64,
            previous={
                "sha256": "a" * 64,
                "raster": {"pages": [page_entry(page)]},
            },
        )


def test_committed_demo_pages_reject_noncanonical_png(tmp_path: Path) -> None:
    namespace = load_generator(tmp_path)
    output = io.BytesIO()
    metadata = PngInfo()
    metadata.add_text("unexpected", "metadata")
    Image.new("RGB", (4, 3), (255, 255, 255)).save(
        output, format="PNG", pnginfo=metadata, optimize=False, compress_level=6
    )
    page = output.getvalue()
    page_path = tmp_path / "lesson" / "pages" / "1.png"
    page_path.parent.mkdir(parents=True)
    page_path.write_bytes(page)

    with pytest.raises(RuntimeError, match="not canonical"):
        namespace["_committed_pages"](
            slug="lesson",
            source_sha256="a" * 64,
            previous={
                "sha256": "a" * 64,
                "raster": {"pages": [page_entry(page)]},
            },
        )


def test_committed_png_structure_checks_crc_lengths_bounds_and_multiple_idat(
    tmp_path: Path,
) -> None:
    namespace = load_generator(tmp_path)
    validate = namespace["_has_canonical_committed_png_structure"]
    page = canonical_test_png()
    assert validate(page) is True

    idat_offset = page.index(b"IDAT")
    idat_length = int.from_bytes(page[idat_offset - 4 : idat_offset], "big")
    idat_crc = idat_offset + 4 + idat_length
    corrupt_idat_crc = bytearray(page)
    corrupt_idat_crc[idat_crc] ^= 1
    assert validate(bytes(corrupt_idat_crc)) is False

    iend_offset = page.index(b"IEND")
    corrupt_iend_crc = bytearray(page)
    corrupt_iend_crc[iend_offset + 4] ^= 1
    assert validate(bytes(corrupt_iend_crc)) is False
    assert validate(replace_png_chunk(page, b"IEND", png_chunk(b"IEND", b"x"))) is False

    invalid_ihdr = bytearray(page)
    invalid_ihdr[4:8] = (12).to_bytes(4, "big")
    assert validate(bytes(invalid_ihdr)) is False
    assert validate(page[:-1]) is False
    assert validate(page + b"trailing") is False

    data_start = idat_offset + 4
    data = page[data_start : data_start + idat_length]
    midpoint = max(1, len(data) // 2)
    multiple_idat = replace_png_chunk(
        page,
        b"IDAT",
        png_chunk(b"IDAT", data[:midpoint]) + png_chunk(b"IDAT", data[midpoint:]),
    )
    assert validate(multiple_idat) is True


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
