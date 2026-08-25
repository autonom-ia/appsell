"""Prove a document is the format it claims to be, before anyone can download it.

An extension is a claim, and a MIME type sent by a browser is a claim. Neither is
evidence. Office documents are ZIP containers, so a macro bearing file renamed to
``.docx`` looks identical from the outside; the only way to refuse it is to open
the container and read what is actually inside.

Three refusals live here. A container that carries a macro project is rejected
whatever it is called. A container that expands far beyond its compressed size,
or holds an implausible number of entries, is rejected before it can exhaust the
worker. And a text format that is not valid UTF-8, or that hides a NUL, is
rejected rather than passed along to a reader that would have to guess.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

from .asset_media import (
    DOCX_MEDIA_TYPE,
    PPTX_MEDIA_TYPE,
    XLSX_MEDIA_TYPE,
    is_document,
    max_bytes,
)


class DocumentRejected(ValueError):
    """The uploaded bytes are not the document they claim to be."""


_ZIP_MAGIC: Final = b"PK\x03\x04"
_EMPTY_ZIP_MAGIC: Final = b"PK\x05\x06"
_CONTENT_TYPES_PART: Final = "[Content_Types].xml"
_REQUIRED_PART: Final = {
    DOCX_MEDIA_TYPE: "word/document.xml",
    XLSX_MEDIA_TYPE: "xl/workbook.xml",
    PPTX_MEDIA_TYPE: "ppt/presentation.xml",
}
_MACRO_MARKERS: Final = ("vbaproject.bin", "vbadata.xml", "ms-office.vbaproject")
_MAX_ENTRIES: Final = 4_096
_MAX_EXPANDED_BYTES: Final = 256 * 1024 * 1024
_TEXT_MEDIA: Final = ("text/plain", "text/markdown", "text/csv", "application/json")
_CHUNK: Final = 256 * 1024
_LOCAL_HEADER: Final = b"PK\x03\x04"
_DESCRIPTOR: Final = b"PK\x07\x08"
_EOCD: Final = b"PK\x05\x06"
_ZIP64_LOCATOR: Final = b"PK\x06\x07"
_EOCD_SIZE: Final = 22
_LOCAL_HEADER_SIZE: Final = 30


@dataclass(frozen=True)
class SpooledDocument:
    path: Path
    byte_size: int
    sha256_hex: str


@contextmanager
def spooled_document_file(
    stream: IO[bytes],
    *,
    expected_size: int,
    expected_sha256_hex: str,
    declared_content_type: str,
) -> Iterator[SpooledDocument]:
    if not is_document(declared_content_type):
        raise DocumentRejected("declared media type is not a document")
    ceiling = max_bytes(declared_content_type)
    if expected_size < 1 or expected_size > ceiling:
        raise DocumentRejected("document size is outside the allowed range")
    if len(expected_sha256_hex) != 64 or any(
        character not in "0123456789abcdef" for character in expected_sha256_hex
    ):
        raise DocumentRejected("document digest is malformed")

    handle, raw_path = tempfile.mkstemp(prefix="appsell-document-")
    path = Path(raw_path)
    try:
        digest = hashlib.sha256()
        written = 0
        with os.fdopen(handle, "wb") as sink:
            os.chmod(path, 0o600)
            while True:
                chunk = stream.read(_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > ceiling:
                    raise DocumentRejected("document exceeded the allowed size while reading")
                digest.update(chunk)
                sink.write(chunk)
            sink.flush()
            os.fsync(sink.fileno())
        if written != expected_size:
            raise DocumentRejected("document size differs from the upload intent")
        if digest.hexdigest() != expected_sha256_hex:
            raise DocumentRejected("document digest differs from the upload intent")
        _validate_document(path, declared_content_type)
        yield SpooledDocument(path=path, byte_size=written, sha256_hex=digest.hexdigest())
    finally:
        path.unlink(missing_ok=True)


def _validate_document(path: Path, media_type: str) -> None:
    if media_type in _REQUIRED_PART:
        _validate_ooxml(path, media_type)
        return
    if media_type in _TEXT_MEDIA:
        _validate_text(path, media_type)
        return
    raise DocumentRejected("document media type has no validator")


def _validate_ooxml(path: Path, media_type: str) -> None:
    with path.open("rb") as handle:
        prefix = handle.read(4)
    if prefix != _ZIP_MAGIC:
        if prefix == _EMPTY_ZIP_MAGIC:
            raise DocumentRejected("office document container is empty")
        raise DocumentRejected("office document is not a container")

    try:
        with zipfile.ZipFile(path) as container:
            entries = container.infolist()
            if len(entries) > _MAX_ENTRIES:
                raise DocumentRejected("office document holds too many parts")
            names: list[str] = []
            declared = 0
            for entry in entries:
                name = entry.filename
                if name.startswith("/") or ".." in Path(name).parts or "\\" in name:
                    raise DocumentRejected("office document part name is unsafe")
                # The classic bomb declares an enormous size so the consumer
                # allocates for it. Refusing on the declared number stops that
                # before a single part is read.
                declared += entry.file_size
                if declared > _MAX_EXPANDED_BYTES:
                    raise DocumentRejected("office document expands beyond the allowed size")
                names.append(name.lower())
            # Two parts under one name let a reader and this validator disagree
            # about which copy is real, which is exactly how a macro declaration
            # hides behind a clean duplicate.
            if len(set(names)) != len(names):
                raise DocumentRejected("office document repeats a part name")
            if _CONTENT_TYPES_PART.lower() not in names:
                raise DocumentRejected("office document has no content type manifest")
            if _REQUIRED_PART[media_type].lower() not in names:
                raise DocumentRejected("office document does not match its declared type")
            if any(marker in name for name in names for marker in _MACRO_MARKERS):
                raise DocumentRejected("office document carries a macro project")
            manifest = container.read(_CONTENT_TYPES_PART).decode("utf-8", "replace").lower()
            if any(marker in manifest for marker in _MACRO_MARKERS):
                raise DocumentRejected("office document declares a macro part")
            _validate_layout(path, entries)
            _measure_expansion(container, entries)
    except zipfile.BadZipFile as error:
        raise DocumentRejected("office document container is unreadable") from error


def _validate_layout(path: Path, entries: list[zipfile.ZipInfo]) -> None:
    """Refuse a container holding bytes its own index does not describe.

    Every guard above reads the archive through its central directory, which is
    a table of contents the author writes. A local header planted before that
    table, with no matching row in it, is invisible to all of them: the file
    passes every check and is then stored unchanged, carrying whatever was
    hidden. The defence is to stop trusting the table as a description of the
    file and verify it against the bytes: entries contiguous from the first one,
    ending exactly where the directory begins, and nothing after the closing
    record.
    """
    payload = path.read_bytes()
    size = len(payload)
    end = payload.rfind(_EOCD)
    if end < 0 or end + _EOCD_SIZE > size:
        raise DocumentRejected("office document has no closing record")
    if payload.rfind(_ZIP64_LOCATOR) >= 0:
        # The 64 bit layout carries a second directory this check does not model,
        # and no support material within the size ceilings needs it.
        raise DocumentRejected("office document uses an unsupported container layout")
    directory_size, directory_offset, comment_length = struct.unpack_from("<IIH", payload, end + 12)
    if end + _EOCD_SIZE + comment_length != size:
        raise DocumentRejected("office document carries bytes after its closing record")
    if directory_offset + directory_size != end:
        raise DocumentRejected("office document index does not meet its closing record")

    cursor = 0
    for entry in sorted(entries, key=lambda item: item.header_offset):
        if entry.header_offset != cursor:
            raise DocumentRejected("office document hides bytes between its parts")
        if payload[cursor : cursor + 4] != _LOCAL_HEADER:
            raise DocumentRejected("office document part header is missing")
        (flags,) = struct.unpack_from("<H", payload, cursor + 6)
        name_length, extra_length = struct.unpack_from("<HH", payload, cursor + 26)
        cursor += _LOCAL_HEADER_SIZE + name_length + extra_length + entry.compress_size
        if flags & 0x08:
            if payload[cursor : cursor + 4] == _DESCRIPTOR:
                cursor += 4
            cursor += 12
    if cursor != directory_offset:
        raise DocumentRejected("office document hides bytes before its index")


def _measure_expansion(container: zipfile.ZipFile, entries: list[zipfile.ZipInfo]) -> None:
    """Measure what the parts really expand to, alongside what they claim.

    The declared size stops the classic bomb, which inflates that field so the
    consumer allocates for it. This second pass covers the opposite lie: an
    archive whose header understates its content. Together they mean neither
    number alone has to be trusted, and the read stops at the ceiling rather than
    after the damage.
    """
    expanded = 0
    for entry in entries:
        if entry.is_dir():
            continue
        with container.open(entry) as part:
            while True:
                chunk = part.read(_CHUNK)
                if not chunk:
                    break
                expanded += len(chunk)
                if expanded > _MAX_EXPANDED_BYTES:
                    raise DocumentRejected("office document expands beyond the allowed size")


def _validate_text(path: Path, media_type: str) -> None:
    payload = path.read_bytes()
    if b"\x00" in payload:
        raise DocumentRejected("text document contains a null byte")
    try:
        decoded = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DocumentRejected("text document is not valid UTF-8") from error
    if media_type == "application/json":
        try:
            json.loads(decoded)
        except ValueError as error:
            raise DocumentRejected("json document does not parse") from error
