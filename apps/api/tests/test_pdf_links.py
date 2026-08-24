from __future__ import annotations

import hashlib
from pathlib import Path

import pikepdf

from appsell_api.pdf_links import extract_pdf_link_manifest, valid_pdf_link_manifest


def test_extracts_internal_and_video_links_without_exposing_active_pdf_actions(
    tmp_path: Path,
) -> None:
    source = tmp_path / "linked.pdf"
    with pikepdf.new() as document:
        first = document.add_blank_page(page_size=(500, 700))
        second = document.add_blank_page(page_size=(500, 700))
        destination = pikepdf.Array([second.obj, pikepdf.Name("/XYZ"), 0, 700, 0])
        document.Root["/Names"] = pikepdf.Dictionary(
            Dests=pikepdf.Dictionary(Names=pikepdf.Array([pikepdf.String("chapter"), destination]))
        )
        video_url = "https://media.example.test/course/lesson.mov"
        first.obj["/Annots"] = pikepdf.Array(
            [
                _link(document, [50, 600, 450, 650], destination="chapter"),
                _link(document, [50, 300, 450, 500], contents=video_url),
                _link(document, [80, 330, 220, 390], contents=video_url),
                _link(document, [50, 200, 450, 240], contents="http://unsafe.test/file.mov"),
            ]
        )
        document.save(source, static_id=True, deterministic_id=True)

    payload = source.read_bytes()
    manifest = extract_pdf_link_manifest(
        source,
        expected_size=len(payload),
        expected_sha256_hex=hashlib.sha256(payload).hexdigest(),
    )

    assert valid_pdf_link_manifest(manifest, expected_page_count=2)
    assert len(manifest["links"]) == 2
    page_link = next(link for link in manifest["links"] if link["kind"] == "page")
    video_link = next(link for link in manifest["links"] if link["kind"] == "video")
    assert page_link["target_page"] == 2
    assert page_link["rect"] == {
        "left": 10.0,
        "top": 7.1429,
        "width": 80.0,
        "height": 7.1429,
    }
    assert video_link["target_url"] == video_url
    assert video_link["rect"] == {
        "left": 10.0,
        "top": 28.5714,
        "width": 80.0,
        "height": 28.5714,
    }
    assert all("http://" not in str(link) for link in manifest["links"])


def _link(
    document: pikepdf.Pdf,
    rect: list[int],
    *,
    destination: str | None = None,
    contents: str | None = None,
) -> pikepdf.Object:
    annotation = pikepdf.Dictionary(
        Type=pikepdf.Name("/Annot"),
        Subtype=pikepdf.Name("/Link"),
        Rect=pikepdf.Array(rect),
        BS=pikepdf.Dictionary(W=0),
    )
    if destination is not None:
        annotation["/Dest"] = pikepdf.String(destination)
    if contents is not None:
        annotation["/Contents"] = pikepdf.String(contents)
    return document.make_indirect(annotation)
