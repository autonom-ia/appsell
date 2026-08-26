from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import httpx
import pikepdf

from .asset_media import PDF_MEDIA_TYPE
from .asset_media import max_bytes as _catalogue_max_bytes

# Derivado do catalogo, nao repetido. Este numero ja viveu em quatro
# arquivos ao mesmo tempo, e duas copias podiam divergir sem nada acusar.
MAX_PDF_BYTES = _catalogue_max_bytes(PDF_MEDIA_TYPE)
PDF_MIME = "application/pdf"
_CHUNK_BYTES = 64 * 1024
_PDF_HEADER = re.compile(rb"%PDF-[12][.][0-9]\Z")
_EOF = re.compile(rb"%%EOF[\x09\x0a\x0c\x0d\x20]*\Z")
_ACTIVE_NAMES = frozenset(
    {
        "/AA",
        "/EmbeddedFile",
        "/EmbeddedFiles",
        "/Filespec",
        "/GoToR",
        "/ImportData",
        "/JavaScript",
        "/JS",
        "/Launch",
        "/OpenAction",
        "/RichMedia",
        "/SubmitForm",
        "/URI",
        "/XFA",
    }
)
_REMOTE_BLOCKED_NAMES = _ACTIVE_NAMES | {"/ObjStm"}
_MAX_PDF_OBJECTS = 20_000
_MAX_PDF_GRAPH_VISITS = 100_000


class PdfRejected(ValueError):
    pass


class PdfSandboxUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedPdf:
    source_path: Path
    ready_path: Path
    source_byte_size: int
    source_sha256_hex: str
    ready_byte_size: int
    ready_sha256_hex: str
    page_count: int


@dataclass(frozen=True)
class SpooledPdf:
    path: Path
    byte_size: int
    sha256_hex: str


@dataclass(frozen=True)
class NormalizedPdf:
    path: Path
    byte_size: int
    sha256_hex: str
    page_count: int


@contextmanager
def verified_pdf_file(
    source: BinaryIO,
    *,
    expected_size: int,
    expected_sha256_hex: str,
    declared_content_type: str,
    temporary_directory: str | None = None,
    isolated_service_boundary: bool = False,
) -> Iterator[VerifiedPdf]:
    with spooled_pdf_file(
        source,
        expected_size=expected_size,
        expected_sha256_hex=expected_sha256_hex,
        declared_content_type=declared_content_type,
        temporary_directory=temporary_directory,
    ) as spooled:
        with normalized_pdf_file(
            spooled.path,
            temporary_directory=temporary_directory,
            isolated_service_boundary=isolated_service_boundary,
        ) as normalized:
            yield VerifiedPdf(
                source_path=spooled.path,
                ready_path=normalized.path,
                source_byte_size=spooled.byte_size,
                source_sha256_hex=spooled.sha256_hex,
                ready_byte_size=normalized.byte_size,
                ready_sha256_hex=normalized.sha256_hex,
                page_count=normalized.page_count,
            )


@contextmanager
def spooled_pdf_file(
    source: BinaryIO,
    *,
    expected_size: int,
    expected_sha256_hex: str,
    declared_content_type: str,
    temporary_directory: str | None = None,
) -> Iterator[SpooledPdf]:
    if declared_content_type != PDF_MIME:
        raise PdfRejected("declared MIME is not application/pdf")
    if expected_size < 1 or expected_size > MAX_PDF_BYTES:
        raise PdfRejected("declared PDF size is outside the allowed range")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256_hex):
        raise PdfRejected("expected SHA-256 is invalid")

    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b", prefix="appsell-pdf-", suffix=".pdf", dir=temporary_directory, delete=False
        ) as destination:
            path = Path(destination.name)
            os.chmod(path, 0o600)
            digest = hashlib.sha256()
            byte_size = 0
            first = b""
            tail = b""
            while True:
                chunk = source.read(_CHUNK_BYTES)
                if not chunk:
                    break
                byte_size += len(chunk)
                if byte_size > MAX_PDF_BYTES or byte_size > expected_size:
                    raise PdfRejected("PDF exceeds its declared or maximum size")
                if not first:
                    first = chunk[:8]
                tail = (tail + chunk)[-4096:]
                digest.update(chunk)
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())

        if byte_size != expected_size:
            raise PdfRejected("PDF size does not match declaration")
        if digest.hexdigest() != expected_sha256_hex:
            raise PdfRejected("PDF SHA-256 does not match declaration")
        if not _PDF_HEADER.fullmatch(first):
            raise PdfRejected("PDF magic bytes are invalid")
        if not _EOF.search(tail):
            raise PdfRejected("PDF has missing EOF or trailing polyglot content")

        yield SpooledPdf(path=path, byte_size=byte_size, sha256_hex=digest.hexdigest())
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


@contextmanager
def normalized_pdf_file(
    source_path: Path,
    *,
    temporary_directory: str | None = None,
    isolated_service_boundary: bool = False,
) -> Iterator[NormalizedPdf]:
    normalized_path: Path | None = None
    normalized_directory: Path | None = None
    try:
        normalized_directory = Path(
            tempfile.mkdtemp(prefix="appsell-pdf-sandbox-", dir=temporary_directory)
        )
        os.chmod(normalized_directory, 0o700)
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix="appsell-pdf-normalized-",
            suffix=".pdf",
            dir=normalized_directory,
            delete=False,
        ) as normalized:
            normalized_path = Path(normalized.name)
        os.chmod(normalized_path, 0o600)
        remote_url = os.environ.get("APPSELL_API_PDF_SANDBOX_URL", "").strip()
        remote_socket = os.environ.get("APPSELL_API_PDF_SANDBOX_SOCKET", "").strip()
        if remote_socket:
            page_count = _remote_normalize_pdf(
                source_path,
                normalized_path,
                "http://pdf-sandbox/normalize",
                socket_path=remote_socket,
            )
        elif remote_url:
            page_count = _remote_normalize_pdf(source_path, normalized_path, remote_url)
        elif isolated_service_boundary:
            try:
                completed = subprocess.run(  # noqa: S603 - command is locally constructed
                    _sandbox_command(source_path, normalized_path),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    check=False,
                    timeout=12,
                    close_fds=True,
                    env=_sandbox_environment(),
                )
            except subprocess.TimeoutExpired as error:
                raise PdfRejected("PDF parser exceeded its time limit") from error
            except OSError as error:
                raise PdfRejected("PDF parser sandbox could not start") from error
            if completed.returncode != 0 or len(completed.stdout) > 1_024:
                raise PdfRejected("PDF parser sandbox rejected active or unsafe content")
            try:
                report = json.loads(completed.stdout)
                page_count = report["page_count"]
            except (json.JSONDecodeError, KeyError, TypeError) as error:
                raise PdfRejected("PDF parser sandbox returned an invalid result") from error
        else:
            raise PdfSandboxUnavailable("isolated PDF parser service is not configured")
        if type(page_count) is not int or not 1 <= page_count <= 5_000:
            raise PdfRejected("PDF parser sandbox returned an invalid page count")
        ready_size, ready_sha256 = _bounded_file_digest(normalized_path)

        yield NormalizedPdf(
            path=normalized_path,
            byte_size=ready_size,
            sha256_hex=ready_sha256,
            page_count=page_count,
        )
    finally:
        if normalized_path is not None:
            normalized_path.unlink(missing_ok=True)
        if normalized_directory is not None:
            normalized_directory.rmdir()


def _validate_pdf_structure(
    path: Path, *, allowed_active_names: frozenset[str] = frozenset()
) -> int:
    try:
        with path.open("rb") as source:
            header = source.read(8)
            source.seek(max(0, path.stat().st_size - 4096))
            tail = source.read(4096)
        if not _PDF_HEADER.fullmatch(header) or not _EOF.search(tail):
            raise PdfRejected("normalized PDF framing is invalid")
        with pikepdf.Pdf.open(path, attempt_recovery=False) as document:
            if document.is_encrypted:
                raise PdfRejected("encrypted PDFs are not accepted")
            _reject_active_objects(document, allowed_active_names=allowed_active_names)
            page_count = len(document.pages)
            if page_count < 1:
                raise PdfRejected("PDF must contain at least one page")
            if page_count > 5_000:
                raise PdfRejected("PDF page count exceeds the structural limit")
            return page_count
    except PdfRejected:
        raise
    except (pikepdf.PdfError, OSError, ValueError) as error:
        raise PdfRejected("PDF structure is invalid") from error


def _bounded_file_digest(path: Path) -> tuple[int, str]:
    size = path.stat().st_size
    if size < 1 or size > MAX_PDF_BYTES:
        raise PdfRejected("normalized PDF size is outside the allowed range")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK_BYTES):
            digest.update(chunk)
    return size, digest.hexdigest()


def _reject_active_objects(
    document: pikepdf.Pdf, *, allowed_active_names: frozenset[str] = frozenset()
) -> None:
    if len(document.objects) > _MAX_PDF_OBJECTS:
        raise PdfRejected("PDF object graph is too large")
    stack = [document.Root]
    stack.extend(document.objects)
    seen_indirect: set[tuple[int, int]] = set()
    inspected = 0
    while stack:
        item = stack.pop()
        object_generation = getattr(item, "objgen", (0, 0))
        if object_generation != (0, 0):
            if object_generation in seen_indirect:
                continue
            seen_indirect.add(object_generation)
        inspected += 1
        if inspected > _MAX_PDF_GRAPH_VISITS:
            raise PdfRejected("PDF object graph is too large")
        if isinstance(item, pikepdf.Dictionary):
            if len(stack) + len(item) > _MAX_PDF_GRAPH_VISITS:
                raise PdfRejected("PDF object graph is too large")
            names = {str(name) for name in item.keys()}
            blocked = (names & _ACTIVE_NAMES) - allowed_active_names
            if blocked:
                raise PdfRejected(f"PDF contains active content: {sorted(blocked)[0]}")
            for value_key in ("/S", "/Type", "/Subtype"):
                active_value = item.get(value_key)
                if (
                    active_value is not None
                    and str(active_value) in _ACTIVE_NAMES
                    and str(active_value) not in allowed_active_names
                ):
                    raise PdfRejected(f"PDF contains active object: {active_value}")
            stack.extend(item[name] for name in item.keys())
        elif isinstance(item, pikepdf.Array):
            if len(stack) + len(item) > _MAX_PDF_GRAPH_VISITS:
                raise PdfRejected("PDF object graph is too large")
            stack.extend(item)


_SANDBOX_BOOTSTRAP = r"""
import os, resource, runpy, sys
resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
resource.setrlimit(resource.RLIMIT_FSIZE, (20 * 1024 * 1024, 20 * 1024 * 1024))
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
if sys.platform.startswith('linux'):
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
sys.path.insert(0, sys.argv.pop(1))
runpy.run_module('appsell_api.pdf_sandbox', run_name='__main__')
"""


def _sandbox_environment() -> dict[str, str]:
    allowed = ("PATH", "PYTHONPATH", "PYTHONHOME", "LANG", "LC_ALL", "TMPDIR")
    environment = {name: os.environ[name] for name in allowed if name in os.environ}
    environment.update(
        {
            "HOME": "/nonexistent",
            "NO_PROXY": "*",
            "http_proxy": "",
            "https_proxy": "",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
        }
    )
    return environment


def _remote_normalize_pdf(
    source: Path,
    destination: Path,
    endpoint: str,
    *,
    socket_path: str = "",
) -> int:
    try:
        parsed = httpx.URL(endpoint)
    except (TypeError, ValueError) as error:
        raise PdfSandboxUnavailable("PDF parser isolation endpoint is invalid") from error
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.userinfo
        or not parsed.host
        or parsed.path != "/normalize"
        or parsed.query
        or parsed.fragment
    ):
        raise PdfSandboxUnavailable("PDF parser isolation endpoint is invalid")
    source_size = source.stat().st_size
    source_sha256 = _bounded_file_digest(source)[1]
    written = 0
    try:
        with (
            source.open("rb") as payload,
            _pdf_sandbox_stream(
                endpoint,
                payload=payload,
                source_size=source_size,
                source_sha256=source_sha256,
                socket_path=socket_path,
            ) as response,
        ):
            if response.status_code in {413, 415, 422}:
                raise PdfRejected("PDF parser isolation service rejected the document")
            if response.status_code != 200:
                raise PdfSandboxUnavailable("PDF parser isolation service is unavailable")
            try:
                page_count = int(response.headers["x-appsell-page-count"])
                expected_ready_sha256 = response.headers["x-appsell-sha256"]
            except (KeyError, ValueError) as error:
                raise PdfSandboxUnavailable(
                    "PDF parser isolation service returned invalid metadata"
                ) from error
            digest = hashlib.sha256()
            with destination.open("wb") as normalized:
                for chunk in response.iter_bytes(_CHUNK_BYTES):
                    written += len(chunk)
                    if written > MAX_PDF_BYTES:
                        raise PdfSandboxUnavailable(
                            "PDF parser isolation service exceeded the response limit"
                        )
                    digest.update(chunk)
                    normalized.write(chunk)
                normalized.flush()
                os.fsync(normalized.fileno())
    except (PdfRejected, PdfSandboxUnavailable):
        raise
    except (httpx.HTTPError, OSError) as error:
        raise PdfSandboxUnavailable("PDF parser isolation service is unavailable") from error
    if written < 1:
        raise PdfSandboxUnavailable("PDF parser isolation service returned an empty document")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_ready_sha256):
        raise PdfSandboxUnavailable("PDF parser isolation service returned an invalid hash")
    if digest.hexdigest() != expected_ready_sha256:
        raise PdfSandboxUnavailable("PDF parser isolation response hash does not match")
    if not 1 <= page_count <= 5_000:
        raise PdfSandboxUnavailable("PDF parser isolation service returned invalid page metadata")
    _validate_remote_framing(destination)
    return page_count


@contextmanager
def _pdf_sandbox_stream(
    endpoint: str,
    *,
    payload: BinaryIO,
    source_size: int,
    source_sha256: str,
    socket_path: str,
) -> Iterator[httpx.Response]:
    headers = {
        "content-type": PDF_MIME,
        "x-appsell-byte-size": str(source_size),
        "x-appsell-sha256": source_sha256,
    }
    timeout = httpx.Timeout(15.0, connect=3.0)
    if socket_path:
        transport = httpx.HTTPTransport(uds=socket_path, retries=0)
        with httpx.Client(
            transport=transport,
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            with client.stream("POST", endpoint, content=payload, headers=headers) as response:
                yield response
        return
    with httpx.stream(
        "POST",
        endpoint,
        content=payload,
        headers=headers,
        timeout=timeout,
        trust_env=False,
        follow_redirects=False,
    ) as response:
        yield response


def _validate_remote_framing(path: Path) -> None:
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise PdfSandboxUnavailable("PDF parser isolation response is unreadable") from error
    if not _PDF_HEADER.fullmatch(payload[:8]) or not _EOF.search(payload[-4096:]):
        raise PdfSandboxUnavailable("PDF parser isolation response framing is invalid")
    if any(name in _REMOTE_BLOCKED_NAMES for name in _structural_pdf_names(payload)):
        raise PdfSandboxUnavailable("PDF parser isolation response contains active content")


def _structural_pdf_names(payload: bytes) -> Iterator[str]:
    index = 0
    length = len(payload)
    delimiters = b"()<>[]{}/%"
    whitespace = b"\x00\x09\x0a\x0c\x0d\x20"
    dictionary_depth = 0
    dictionary_start = -1
    last_dictionary: tuple[int, int] | None = None
    while index < length:
        byte = payload[index]
        if byte == ord("%"):
            index = _skip_pdf_comment(payload, index + 1)
            continue
        if byte == ord("("):
            index = _skip_pdf_literal_string(payload, index + 1)
            continue
        if payload.startswith(b"<<", index):
            if dictionary_depth == 0:
                dictionary_start = index
            dictionary_depth += 1
            index += 2
            continue
        if payload.startswith(b">>", index):
            if dictionary_depth < 1:
                raise _remote_stream_framing_error()
            dictionary_depth -= 1
            index += 2
            if dictionary_depth == 0:
                last_dictionary = (dictionary_start, index)
            continue
        if byte == ord("<"):
            closing = payload.find(b">", index + 1)
            if closing < 0:
                raise _remote_stream_framing_error()
            index = closing + 1
            continue
        if (
            payload.startswith(b"stream", index)
            and _pdf_token_boundary(payload, index - 1)
            and _pdf_token_boundary(payload, index + 6)
        ):
            if dictionary_depth or last_dictionary is None:
                raise _remote_stream_framing_error()
            dictionary_begin, dictionary_end = last_dictionary
            if not _pdf_whitespace_or_comments(payload, dictionary_end, index):
                raise _remote_stream_framing_error()
            stream_length = _direct_stream_length(payload[dictionary_begin:dictionary_end])
            body_start = _stream_body_start(payload, index + 6)
            body_end = body_start + stream_length
            if body_end > length:
                raise _remote_stream_framing_error()
            marker = _stream_end_marker(payload, body_end)
            index = marker + 9
            last_dictionary = None
            continue
        if byte == ord("/"):
            end = index + 1
            while (
                end < length and payload[end] not in whitespace and payload[end] not in delimiters
            ):
                end += 1
            raw_name = payload[index + 1 : end]
            decoded = re.sub(
                rb"#([0-9A-Fa-f]{2})",
                lambda match: bytes([int(match.group(1), 16)]),
                raw_name,
            )
            try:
                yield "/" + decoded.decode("latin-1")
            except UnicodeDecodeError:
                pass
            index = max(end, index + 1)
            continue
        index += 1
    if dictionary_depth:
        raise _remote_stream_framing_error()


def _remote_stream_framing_error() -> PdfSandboxUnavailable:
    return PdfSandboxUnavailable("PDF parser isolation response stream framing is invalid")


def _skip_pdf_comment(payload: bytes, index: int) -> int:
    carriage_return = payload.find(b"\r", index)
    line_feed = payload.find(b"\n", index)
    endings = [ending for ending in (carriage_return, line_feed) if ending >= 0]
    if not endings:
        return len(payload)
    ending = min(endings)
    return ending + (2 if payload[ending : ending + 2] == b"\r\n" else 1)


def _pdf_whitespace_or_comments(payload: bytes, start: int, end: int) -> bool:
    index = start
    whitespace = b"\x00\x09\x0a\x0c\x0d\x20"
    while index < end:
        if payload[index] in whitespace:
            index += 1
        elif payload[index] == ord("%"):
            index = _skip_pdf_comment(payload[:end], index + 1)
        else:
            return False
    return index == end


def _direct_stream_length(dictionary: bytes) -> int:
    index = 2
    depth = 1
    array_depth = 0
    found_length: int | None = None
    while index < len(dictionary) - 2:
        if dictionary[index] == ord("%"):
            index = _skip_pdf_comment(dictionary, index + 1)
            continue
        if dictionary[index] == ord("("):
            index = _skip_pdf_literal_string(dictionary, index + 1)
            continue
        if dictionary.startswith(b"<<", index):
            depth += 1
            index += 2
            continue
        if dictionary.startswith(b">>", index):
            depth -= 1
            index += 2
            continue
        if dictionary[index] == ord("<"):
            closing = dictionary.find(b">", index + 1)
            if closing < 0:
                raise _remote_stream_framing_error()
            index = closing + 1
            continue
        if dictionary[index] == ord("["):
            array_depth += 1
            index += 1
            continue
        if dictionary[index] == ord("]"):
            array_depth = max(0, array_depth - 1)
            index += 1
            continue
        if dictionary[index] != ord("/") or depth != 1 or array_depth:
            index += 1
            continue
        name_end = index + 1
        while name_end < len(dictionary) and not _pdf_token_boundary(dictionary, name_end):
            name_end += 1
        decoded_name = re.sub(
            rb"#([0-9A-Fa-f]{2})",
            lambda match: bytes([int(match.group(1), 16)]),
            dictionary[index + 1 : name_end],
        )
        index = name_end
        if decoded_name != b"Length":
            continue
        if found_length is not None:
            raise _remote_stream_framing_error()
        index = _skip_pdf_space_and_comments(dictionary, index)
        value_end = index
        while value_end < len(dictionary) and dictionary[value_end : value_end + 1].isdigit():
            value_end += 1
        if value_end == index:
            raise _remote_stream_framing_error()
        found_length = int(dictionary[index:value_end])
        following = _skip_pdf_space_and_comments(dictionary, value_end)
        if not (
            dictionary.startswith(b">>", following) or dictionary[following : following + 1] == b"/"
        ):
            raise _remote_stream_framing_error()
        index = value_end
    if found_length is None or found_length > MAX_PDF_BYTES:
        raise _remote_stream_framing_error()
    return found_length


def _skip_pdf_space_and_comments(payload: bytes, index: int) -> int:
    whitespace = b"\x00\x09\x0a\x0c\x0d\x20"
    while index < len(payload):
        if payload[index] in whitespace:
            index += 1
        elif payload[index] == ord("%"):
            index = _skip_pdf_comment(payload, index + 1)
        else:
            break
    return index


def _stream_body_start(payload: bytes, index: int) -> int:
    if payload[index : index + 2] == b"\r\n":
        return index + 2
    if payload[index : index + 1] in {b"\r", b"\n"}:
        return index + 1
    raise _remote_stream_framing_error()


def _stream_end_marker(payload: bytes, body_end: int) -> int:
    if payload[body_end : body_end + 2] == b"\r\n":
        marker = body_end + 2
    elif payload[body_end : body_end + 1] in {b"\r", b"\n"}:
        marker = body_end + 1
    else:
        marker = body_end
    if not payload.startswith(b"endstream", marker) or not _pdf_token_boundary(payload, marker + 9):
        raise _remote_stream_framing_error()
    return marker


def _skip_pdf_literal_string(payload: bytes, index: int) -> int:
    depth = 1
    while index < len(payload) and depth:
        byte = payload[index]
        if byte == ord("\\"):
            index += 2
            continue
        if byte == ord("("):
            depth += 1
        elif byte == ord(")"):
            depth -= 1
        index += 1
    return index


def _pdf_token_boundary(payload: bytes, index: int) -> bool:
    if index < 0 or index >= len(payload):
        return True
    return payload[index] in b"\x00\x09\x0a\x0c\x0d\x20()<>[]{}/%"


def _sandbox_command(source_path: Path, destination_path: Path) -> list[str]:
    source = source_path.resolve(strict=True)
    destination = destination_path.resolve(strict=True)
    parser = [
        sys.executable,
        "-I",
        "-c",
        _SANDBOX_BOOTSTRAP,
        str(Path(__file__).resolve().parents[1]),
        str(source),
        str(destination),
    ]
    if sys.platform == "darwin":
        sandbox_exec = shutil.which("sandbox-exec")
        if sandbox_exec is None:
            raise PdfRejected("PDF parser isolation is unavailable")
        policy = "\n".join(
            (
                "(version 1)",
                "(allow default)",
                "(deny network*)",
                "(deny file-write*)",
                f"(allow file-write* (subpath {json.dumps(str(destination.parent))}))",
            )
        )
        return [sandbox_exec, "-p", policy, *parser]
    if sys.platform.startswith("linux"):
        return parser
    raise PdfRejected("PDF parser isolation is unavailable")
