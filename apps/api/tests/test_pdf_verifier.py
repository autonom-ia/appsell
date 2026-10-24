from __future__ import annotations

import hashlib
import io
import json
import subprocess
from pathlib import Path

import pikepdf
import pytest
from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]

from appsell_api import pdf_verifier
from appsell_api.pdf_verifier import (
    PDF_MIME,
    PdfRejected,
)
from appsell_api.pdf_verifier import (
    verified_pdf_file as _verified_pdf_file,
)


def verified_pdf_file(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
    kwargs["isolated_service_boundary"] = True
    return _verified_pdf_file(*args, **kwargs)  # type: ignore[arg-type]


def test_valid_pdf_is_spooled_privately_and_removed_after_use(tmp_path: Path) -> None:
    payload = valid_pdf()
    with verified_pdf_file(
        io.BytesIO(payload),
        expected_size=len(payload),
        expected_sha256_hex=hashlib.sha256(payload).hexdigest(),
        declared_content_type=PDF_MIME,
        temporary_directory=str(tmp_path),
    ) as verified:
        assert verified.page_count == 1
        assert verified.source_path.stat().st_mode & 0o777 == 0o600
        assert verified.ready_path.stat().st_mode & 0o777 == 0o600
        source_path, ready_path = verified.source_path, verified.ready_path
    assert not source_path.exists() and not ready_path.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("mutator", "content_type", "error"),
    [
        (lambda body: b"NOTPDF" + body[6:], PDF_MIME, "magic"),
        (lambda body: body + b"<script>alert(1)</script>", PDF_MIME, "polyglot"),
        (lambda body: body + b"\x00", PDF_MIME, "polyglot"),
        (lambda body: body + b"PK\x03\x04zip", PDF_MIME, "polyglot"),
        (lambda body: body, "text/html", "MIME"),
    ],
)
def test_rejects_false_magic_polyglot_and_false_mime(
    mutator: object, content_type: str, error: str, tmp_path: Path
) -> None:
    payload = mutator(valid_pdf())  # type: ignore[operator]
    with pytest.raises(PdfRejected, match=error):
        with verified_pdf_file(
            io.BytesIO(payload),
            expected_size=len(payload),
            expected_sha256_hex=hashlib.sha256(payload).hexdigest(),
            declared_content_type=content_type,
            temporary_directory=str(tmp_path),
        ):
            pass
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("dictionary_key", "active_name"),
    [
        ("/S", "/JavaScript"),
        ("/S", "/Launch"),
        ("/Type", "/EmbeddedFile"),
        ("/Type", "/Filespec"),
        ("/Subtype", "/RichMedia"),
    ],
)
def test_rejects_active_pdf_objects(dictionary_key: str, active_name: str, tmp_path: Path) -> None:
    source = io.BytesIO(valid_pdf())
    output = io.BytesIO()
    with pikepdf.Pdf.open(source) as document:
        document.Root["/ActiveFixture"] = pikepdf.Dictionary(
            {dictionary_key: pikepdf.Name(active_name)}
        )
        document.save(output)
    payload = output.getvalue()
    with pytest.raises(PdfRejected, match="active"):
        with verified_pdf_file(
            io.BytesIO(payload),
            expected_size=len(payload),
            expected_sha256_hex=hashlib.sha256(payload).hexdigest(),
            declared_content_type=PDF_MIME,
            temporary_directory=str(tmp_path),
        ):
            pass


def test_sanitizes_uri_actions_from_normalized_pdf(tmp_path: Path) -> None:
    source = io.BytesIO(valid_pdf())
    output = io.BytesIO()
    with pikepdf.Pdf.open(source) as document:
        document.Root["/ActiveFixture"] = pikepdf.Dictionary(
            {
                "/A": pikepdf.Dictionary(
                    {
                        "/S": pikepdf.Name("/URI"),
                        "/URI": pikepdf.String("https://example.test/course"),
                    }
                )
            }
        )
        document.save(output)
    payload = output.getvalue()

    with verified_pdf_file(
        io.BytesIO(payload),
        expected_size=len(payload),
        expected_sha256_hex=hashlib.sha256(payload).hexdigest(),
        declared_content_type=PDF_MIME,
        temporary_directory=str(tmp_path),
    ) as verified:
        normalized = verified.ready_path.read_bytes()
        assert b"/URI" not in normalized
        assert b"https://example.test/course" not in normalized


def test_rejects_size_and_hash_mismatch_without_consuming_unbounded_input(tmp_path: Path) -> None:
    payload = valid_pdf()
    with pytest.raises(PdfRejected, match="size"):
        with verified_pdf_file(
            io.BytesIO(payload + b"x"),
            expected_size=len(payload),
            expected_sha256_hex=hashlib.sha256(payload).hexdigest(),
            declared_content_type=PDF_MIME,
            temporary_directory=str(tmp_path),
        ):
            pass
    with pytest.raises(PdfRejected, match="SHA-256"):
        with verified_pdf_file(
            io.BytesIO(payload),
            expected_size=len(payload),
            expected_sha256_hex="0" * 64,
            declared_content_type=PDF_MIME,
            temporary_directory=str(tmp_path),
        ):
            pass


def test_rejects_pdf_when_sandbox_exceeds_time_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = valid_pdf()

    def timeout(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd="pdf-sandbox", timeout=12)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(PdfRejected, match="time limit"):
        with verified_pdf_file(
            io.BytesIO(payload),
            expected_size=len(payload),
            expected_sha256_hex=hashlib.sha256(payload).hexdigest(),
            declared_content_type=PDF_MIME,
            temporary_directory=str(tmp_path),
        ):
            pass
    assert list(tmp_path.iterdir()) == []


# `_sandbox_command` só aplica sandbox do sistema no macOS (`sandbox-exec`). No
# Linux o parser roda sem ele, e quem isola é o contêiner `pdf-sandbox`
# (`isolated_service_boundary=True`). Essa prova é a #468, não este teste.
@pytest.mark.so_macos
def test_os_sandbox_denies_network_and_writes_outside_output_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source.pdf"
    output_directory = tmp_path / "output"
    output_directory.mkdir(mode=0o700)
    destination = output_directory / "result.json"
    outside = tmp_path / "outside.txt"
    source.write_bytes(valid_pdf())
    destination.write_bytes(b"")
    probe = f"""
import errno, json, pathlib, socket, sys
result = {{}}
for name, target in (("source", pathlib.Path(sys.argv[2])),
                     ("outside", pathlib.Path({str(outside)!r}))):
    try:
        target.write_bytes(b"forbidden")
    except OSError as error:
        result[name] = error.errno
    else:
        result[name] = 0
try:
    socket.create_connection(("1.1.1.1", 53), timeout=0.2)
except OSError as error:
    result["network"] = error.errno
else:
    result["network"] = 0
pathlib.Path(sys.argv[3]).write_text(json.dumps(result))
"""
    monkeypatch.setattr(pdf_verifier, "_SANDBOX_BOOTSTRAP", probe)

    completed = subprocess.run(  # noqa: S603 - command is locally constructed
        pdf_verifier._sandbox_command(source, destination),  # noqa: SLF001
        capture_output=True,
        check=False,
        env=pdf_verifier._sandbox_environment(),  # noqa: SLF001
        timeout=3,
    )

    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    result = json.loads(destination.read_text())
    assert result["source"] in {1, 30}
    assert result["outside"] in {1, 30}
    assert result["network"] in {1, 13, 101}
    assert source.read_bytes() == valid_pdf()
    assert not outside.exists()


def test_invalid_200_response_from_sidecar_is_retryable_not_content_rejection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"not-a-pdf-but-hash-matches"

    class FakeResponse:
        status_code = 200
        headers = {
            "x-appsell-page-count": "1",
            "x-appsell-sha256": hashlib.sha256(payload).hexdigest(),
        }

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def iter_bytes(self, _chunk_size: int):  # type: ignore[no-untyped-def]
            yield payload

    monkeypatch.setattr(
        pdf_verifier.httpx,  # type: ignore[attr-defined]
        "stream",
        lambda *_args, **_kwargs: FakeResponse(),
    )
    source = tmp_path / "source.pdf"
    destination = tmp_path / "destination.pdf"
    source.write_bytes(valid_pdf())

    with pytest.raises(pdf_verifier.PdfSandboxUnavailable, match="framing"):
        pdf_verifier._remote_normalize_pdf(  # noqa: SLF001
            source, destination, "https://parser.test/normalize"
        )


def test_remote_parser_uses_configured_unix_socket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = valid_pdf()
    observed: dict[str, object] = {}

    class FakeTransport:
        def __init__(self, *, uds: str, retries: int) -> None:
            observed.update(uds=uds, retries=retries)

    class FakeResponse:
        status_code = 200
        headers = {
            "x-appsell-page-count": "1",
            "x-appsell-sha256": hashlib.sha256(payload).hexdigest(),
        }

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def iter_bytes(self, _chunk_size: int):  # type: ignore[no-untyped-def]
            yield payload

    class FakeClient:
        def __init__(
            self,
            *,
            transport: object,
            timeout: object,
            trust_env: bool,
            follow_redirects: bool,
        ) -> None:
            observed.update(
                transport=transport,
                timeout=timeout,
                trust_env=trust_env,
                follow_redirects=follow_redirects,
            )

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def stream(self, method: str, endpoint: str, **_kwargs: object) -> FakeResponse:
            observed.update(method=method, endpoint=endpoint)
            return FakeResponse()

    monkeypatch.setattr(
        pdf_verifier.httpx,  # type: ignore[attr-defined]
        "HTTPTransport",
        FakeTransport,
    )
    monkeypatch.setattr(pdf_verifier.httpx, "Client", FakeClient)  # type: ignore[attr-defined]
    source = tmp_path / "source.pdf"
    destination = tmp_path / "destination.pdf"
    source.write_bytes(valid_pdf())

    pages = pdf_verifier._remote_normalize_pdf(  # noqa: SLF001
        source,
        destination,
        "http://pdf-sandbox/normalize",
        socket_path="/run/appsell-pdf/pdf.sock",
    )

    assert pages == 1
    assert destination.read_bytes() == payload
    assert observed["uds"] == "/run/appsell-pdf/pdf.sock"
    assert observed["method"] == "POST"
    assert observed["trust_env"] is False
    assert observed["follow_redirects"] is False


def test_active_200_response_from_sidecar_is_retryable_parser_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"%PDF-1.4\n1 0 obj<</S/JavaScript>>endobj\n%%EOF"

    class FakeResponse:
        status_code = 200
        headers = {
            "x-appsell-page-count": "1",
            "x-appsell-sha256": hashlib.sha256(payload).hexdigest(),
        }

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def iter_bytes(self, _chunk_size: int):  # type: ignore[no-untyped-def]
            yield payload

    monkeypatch.setattr(
        pdf_verifier.httpx,  # type: ignore[attr-defined]
        "stream",
        lambda *_args, **_kwargs: FakeResponse(),
    )
    source = tmp_path / "source.pdf"
    destination = tmp_path / "destination.pdf"
    source.write_bytes(valid_pdf())

    with pytest.raises(pdf_verifier.PdfSandboxUnavailable, match="active content"):
        pdf_verifier._remote_normalize_pdf(  # noqa: SLF001
            source, destination, "https://parser.test/normalize"
        )


def test_invalid_page_count_from_sidecar_is_retryable_parser_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = valid_pdf()

    class FakeResponse:
        status_code = 200
        headers = {
            "x-appsell-page-count": "0",
            "x-appsell-sha256": hashlib.sha256(payload).hexdigest(),
        }

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def iter_bytes(self, _chunk_size: int):  # type: ignore[no-untyped-def]
            yield payload

    monkeypatch.setattr(
        pdf_verifier.httpx,  # type: ignore[attr-defined]
        "stream",
        lambda *_args, **_kwargs: FakeResponse(),
    )
    source = tmp_path / "source.pdf"
    destination = tmp_path / "destination.pdf"
    source.write_bytes(payload)

    with pytest.raises(pdf_verifier.PdfSandboxUnavailable, match="page metadata"):
        pdf_verifier._remote_normalize_pdf(  # noqa: SLF001
            source, destination, "https://parser.test/normalize"
        )


def test_structural_name_scan_ignores_names_in_text_metadata_and_streams(
    tmp_path: Path,
) -> None:
    payload = (
        b"%PDF-1.4\n"
        b"% /ObjStm /JavaScript in a comment\n"
        b"1 0 obj<</Title(/URI /JS /ObjStm are ordinary text) "
        b"/Label <2f4f626a53746d>>>endobj\n"
        b"2 0 obj<</Length 25>>stream\n/JavaScript /URI /ObjStm\nendstream\nendobj\n"
        b"%%EOF"
    )
    path = tmp_path / "normalized.pdf"
    path.write_bytes(payload)

    pdf_verifier._validate_remote_framing(path)  # noqa: SLF001


def test_structural_name_scan_decodes_escaped_active_names(tmp_path: Path) -> None:
    path = tmp_path / "normalized.pdf"
    path.write_bytes(b"%PDF-1.4\n1 0 obj<</#55RI(https://example.test)>>endobj\n%%EOF")

    with pytest.raises(pdf_verifier.PdfSandboxUnavailable, match="active content"):
        pdf_verifier._validate_remote_framing(path)  # noqa: SLF001


def test_remote_framing_rejects_active_action_hidden_in_object_stream(
    tmp_path: Path,
) -> None:
    output = io.BytesIO()
    with pikepdf.Pdf.open(io.BytesIO(valid_pdf())) as document:
        action = document.make_indirect(
            pikepdf.Dictionary(
                {
                    "/S": pikepdf.Name("/JavaScript"),
                    "/JS": pikepdf.String("app.alert('blocked')"),
                }
            )
        )
        document.Root["/ActiveFixture"] = action
        document.save(
            output,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
            compress_streams=True,
        )
    payload = output.getvalue()
    assert b"/ObjStm" in payload
    assert b"/JavaScript" not in payload
    path = tmp_path / "normalized-object-stream.pdf"
    path.write_bytes(payload)

    with pytest.raises(pdf_verifier.PdfSandboxUnavailable, match="active content"):
        pdf_verifier._validate_remote_framing(path)  # noqa: SLF001


def test_remote_framing_decodes_escaped_object_stream_name(tmp_path: Path) -> None:
    path = tmp_path / "normalized-escaped-object-stream.pdf"
    path.write_bytes(b"%PDF-1.5\n1 0 obj<</Type /#4fbjStm>>endobj\n%%EOF")

    with pytest.raises(pdf_verifier.PdfSandboxUnavailable, match="active content"):
        pdf_verifier._validate_remote_framing(path)  # noqa: SLF001


@pytest.mark.parametrize("comment_ending", [b"\r", b"\n", b"\r\n"])
def test_structural_name_scan_ends_comments_at_any_pdf_eol(
    comment_ending: bytes, tmp_path: Path
) -> None:
    path = tmp_path / "normalized-comment-eol.pdf"
    path.write_bytes(
        b"%PDF-1.4\n% harmless comment"
        + comment_ending
        + b"1 0 obj<</OpenAction 2 0 R>>endobj\n%%EOF"
    )

    with pytest.raises(pdf_verifier.PdfSandboxUnavailable, match="active content"):
        pdf_verifier._validate_remote_framing(path)  # noqa: SLF001


def test_stream_scanner_uses_direct_length_not_payload_endstream_text(tmp_path: Path) -> None:
    stream_body = b"prefix endstream /JavaScript suffix"
    path = tmp_path / "normalized-stream-payload.pdf"
    path.write_bytes(
        b"%PDF-1.4\n1 0 obj<</Length "
        + str(len(stream_body)).encode()
        + b">>stream\r\n"
        + stream_body
        + b"\r\nendstream\nendobj\n%%EOF"
    )

    pdf_verifier._validate_remote_framing(path)  # noqa: SLF001


@pytest.mark.parametrize(
    "stream_object",
    [
        b"1 0 obj<<>>stream\npayload\nendstream\nendobj\n",
        b"1 0 obj<</Length nope>>stream\npayload\nendstream\nendobj\n",
        b"1 0 obj<</Length 7 0 R>>stream\npayload\nendstream\nendobj\n",
        b"1 0 obj<</Length 99>>stream\npayload\nendstream\nendobj\n",
        b"1 0 obj<</Length 7>>stream\npayload truncated\nendobj\n",
    ],
)
def test_stream_scanner_fails_closed_on_ambiguous_length_or_framing(
    stream_object: bytes, tmp_path: Path
) -> None:
    path = tmp_path / "normalized-invalid-stream.pdf"
    path.write_bytes(b"%PDF-1.4\n" + stream_object + b"%%EOF")

    with pytest.raises(pdf_verifier.PdfSandboxUnavailable, match="stream framing"):
        pdf_verifier._validate_remote_framing(path)  # noqa: SLF001


def valid_pdf() -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(320, 480), invariant=1)
    canvas.drawString(32, 430, "Autonom.ia safe PDF")
    canvas.showPage()
    canvas.save()
    return output.getvalue()
