from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import Any

import pytest

from appsell_api.asset_media import (
    DOCX_MEDIA_TYPE,
    PPTX_MEDIA_TYPE,
    XLSX_MEDIA_TYPE,
    extension_matches,
    is_document,
    max_bytes,
    spec_for,
)
from appsell_api.document_verifier import DocumentRejected, spooled_document_file


def container(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def verify(payload: bytes, media_type: str, **overrides: Any) -> int:
    arguments = {
        "expected_size": len(payload),
        "expected_sha256_hex": hashlib.sha256(payload).hexdigest(),
        "declared_content_type": media_type,
    }
    arguments.update(overrides)
    with spooled_document_file(io.BytesIO(payload), **arguments) as document:  # type: ignore[arg-type]
        return document.byte_size


WORD = {"[Content_Types].xml": "<Types/>", "word/document.xml": "<w/>"}


def test_a_clean_office_document_is_accepted() -> None:
    assert verify(container(WORD), DOCX_MEDIA_TYPE) > 0


def test_a_macro_project_is_refused_whatever_the_file_is_called() -> None:
    # The whole reason this verifier exists: .docm renamed to .docx is
    # indistinguishable from the outside.
    payload = container({**WORD, "word/vbaProject.bin": "macro"})
    with pytest.raises(DocumentRejected, match="macro"):
        verify(payload, DOCX_MEDIA_TYPE)


def test_a_macro_declared_only_in_the_manifest_is_refused() -> None:
    payload = container(
        {
            "[Content_Types].xml": "<Types><Override ContentType='ms-office.vbaProject'/></Types>",
            "word/document.xml": "<w/>",
        }
    )
    with pytest.raises(DocumentRejected, match="macro"):
        verify(payload, DOCX_MEDIA_TYPE)


def test_a_pdf_renamed_to_docx_is_refused() -> None:
    with pytest.raises(DocumentRejected, match="container"):
        verify(b"%PDF-1.7 not a container", DOCX_MEDIA_TYPE)


def test_a_container_of_the_wrong_office_kind_is_refused() -> None:
    # A spreadsheet declared as a document must not pass just because both are
    # ZIP archives with a content type manifest.
    with pytest.raises(DocumentRejected, match="declared type"):
        verify(container(WORD), XLSX_MEDIA_TYPE)


def test_a_container_without_a_manifest_is_refused() -> None:
    with pytest.raises(DocumentRejected, match="content type manifest"):
        verify(container({"word/document.xml": "<w/>"}), DOCX_MEDIA_TYPE)


def test_a_part_escaping_its_directory_is_refused() -> None:
    payload = container({**WORD, "../escape.xml": "x"})
    with pytest.raises(DocumentRejected, match="unsafe"):
        verify(payload, DOCX_MEDIA_TYPE)


def test_a_bomb_is_refused_by_what_it_declares() -> None:
    """The classic bomb inflates its own declared size so consumers allocate.

    Forging that field is exactly what such an archive does, and it is the first
    number the guard reads, so the refusal happens before any part is opened and
    without producing a gigabyte here.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, part in WORD.items():
            archive.writestr(name, part)
        archive.writestr("word/huge.xml", "a")
        archive.filelist[-1].file_size = 512 * 1024 * 1024
    with pytest.raises(DocumentRejected, match="expands"):
        verify(buffer.getvalue(), DOCX_MEDIA_TYPE)


def test_a_bomb_is_also_measured_by_what_it_really_contains() -> None:
    """And the opposite lie is covered by reading the parts.

    An archive that understates its own header would slip past a check that only
    believes the directory, so the second pass reads until the ceiling.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, part in WORD.items():
            archive.writestr(name, part)
        archive.writestr("word/huge.xml", "\0" * (300 * 1024 * 1024))
    bomb = buffer.getvalue()
    assert len(bomb) < 2 * 1024 * 1024, "the bomb must stay small on disk"
    with pytest.raises(DocumentRejected, match="expands"):
        verify(bomb, DOCX_MEDIA_TYPE)


def test_a_repeated_part_name_is_refused() -> None:
    """Two parts under one name let a reader and this validator disagree.

    Python resolves a duplicate name to the last entry, so a macro declaring
    manifest planted first would be checked against a clean copy planted last.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types ContentType='ms-office.vbaProject'/>")
        archive.writestr("word/document.xml", "<w/>")
        archive.writestr("[Content_Types].xml", "<Types/>")
    with pytest.raises(DocumentRejected, match="repeats a part name"):
        verify(buffer.getvalue(), DOCX_MEDIA_TYPE)


def test_a_container_with_too_many_parts_is_refused() -> None:
    parts = {**WORD}
    parts.update({f"word/part{index}.xml": "x" for index in range(4_100)})
    with pytest.raises(DocumentRejected, match="too many parts"):
        verify(container(parts), DOCX_MEDIA_TYPE)


def test_text_must_be_valid_utf8_without_a_null() -> None:
    assert verify(b"linha um\nlinha dois\n", "text/plain") > 0
    with pytest.raises(DocumentRejected, match="UTF-8"):
        verify(b"\xff\xfe invalido", "text/plain")
    with pytest.raises(DocumentRejected, match="null"):
        verify(b"texto\x00oculto", "text/plain")


def test_json_must_parse() -> None:
    assert verify(json.dumps({"ok": True}).encode(), "application/json") > 0
    with pytest.raises(DocumentRejected, match="parse"):
        verify(b"{nao e json}", "application/json")


def test_a_digest_or_size_that_disagrees_with_the_intent_is_refused() -> None:
    payload = container(WORD)
    with pytest.raises(DocumentRejected, match="digest"):
        verify(payload, DOCX_MEDIA_TYPE, expected_sha256_hex="a" * 64)
    with pytest.raises(DocumentRejected, match="size"):
        verify(payload, DOCX_MEDIA_TYPE, expected_size=len(payload) + 1)


def test_the_catalogue_agrees_with_itself() -> None:
    assert is_document(DOCX_MEDIA_TYPE)
    assert not is_document("application/pdf")
    assert not is_document("video/mp4")
    assert max_bytes(PPTX_MEDIA_TYPE) > max_bytes(DOCX_MEDIA_TYPE)
    assert max_bytes("image/gif") == 0
    assert extension_matches(XLSX_MEDIA_TYPE, "planilha.XLSX")
    assert not extension_matches(XLSX_MEDIA_TYPE, "planilha.docx")
    assert spec_for("application/vnd.ms-word.document.macroEnabled.12") is None
