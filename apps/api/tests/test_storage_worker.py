from __future__ import annotations

import base64
import hashlib
import io
import struct
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]

from appsell_api.storage_s3 import StorageCapabilityError, StoredObject
from appsell_api.storage_worker import (
    ClamAvScanner,
    CleanupRetryableError,
    DeterministicLocalScanner,
    PdfStorageWorker,
    ScannerUnavailable,
    ScanRejected,
    VerifyPdfJob,
    assert_storage_worker_ready,
)


def local_worker(storage: Any, scanner: Any) -> PdfStorageWorker:
    return PdfStorageWorker(storage, scanner, isolated_service_boundary=True)


def test_worker_normalizes_to_distinct_db_key_and_preserves_source_until_commit() -> None:
    payload = valid_pdf()
    job = make_job(payload)
    storage = FakeStorage(stored("quarantine", job.source_key, "source-v1", payload, job), payload)
    result = local_worker(storage, DeterministicLocalScanner()).process(job)
    assert storage.put_key == job.ready_key != job.source_key
    assert storage.ready_payload != payload
    assert result.source_sha256_hex == hashlib.sha256(payload).hexdigest()
    assert result.ready_sha256_hex == hashlib.sha256(storage.ready_payload).hexdigest()
    assert result.ready_sha256_hex != result.source_sha256_hex
    assert result.pdf_page_count == 1
    assert len(result.pdf_pages) == 1
    assert result.pdf_pages[0].stored.key.endswith("/v1/pages/1.png")
    assert storage.reconciled_prefixes == [("ready", result.ready.key.rsplit("/", 1)[0] + "/")]
    assert storage.deleted == []
    worker = local_worker(storage, DeterministicLocalScanner())
    worker.cleanup_source_after_commit(result)
    assert storage.deleted == [("quarantine", job.source_key, "source-v1")]


def test_worker_verifies_and_promotes_mp4_without_mutating_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        "appsell_api.storage_worker.probe_video_safely", lambda _path: events.append("probe")
    )
    payload = valid_mp4()
    job = make_job(payload, media_type="video/mp4", file_name="aula.mp4")
    storage = FakeStorage(stored("quarantine", job.source_key, "source-v1", payload, job), payload)
    scanner = RecordingScanner(reject_call=99, events=events)
    result = local_worker(storage, scanner).process(job)
    assert events == ["scan", "probe"]
    assert storage.ready_payload == payload
    assert result.ready.content_type == "video/mp4"
    assert result.source_sha256_hex == result.ready_sha256_hex
    assert result.pdf_page_count == 0


def test_worker_rejects_before_ready_put_on_hash_or_metadata_mismatch() -> None:
    payload = valid_pdf()
    job = make_job(payload)
    source = stored("quarantine", job.source_key, "source-v1", payload, job)
    source = StoredObject(**{**source.__dict__, "checksum_sha256_b64": "invalid"})
    storage = FakeStorage(source, payload)
    with pytest.raises(ScanRejected, match="checksum"):
        local_worker(storage, DeterministicLocalScanner()).process(job)
    assert storage.put_key is None and storage.deleted == []


def test_finalize_failure_compensates_ready_and_preserves_source() -> None:
    payload = valid_pdf()
    job = make_job(payload)
    storage = FakeStorage(stored("quarantine", job.source_key, "source-v1", payload, job), payload)
    worker = local_worker(storage, DeterministicLocalScanner())
    result = worker.process(job)
    worker.compensate_ready_after_finalize_failure(result)
    assert storage.deleted == [
        ("ready", result.pdf_pages[0].stored.key, "page-v1"),
        ("ready", job.ready_key, "ready-v1"),
    ]


def test_source_cleanup_failure_is_retryable_and_never_removes_ready() -> None:
    payload = valid_pdf()
    job = make_job(payload)
    storage = FakeStorage(stored("quarantine", job.source_key, "source-v1", payload, job), payload)
    worker = local_worker(storage, DeterministicLocalScanner())
    result = worker.process(job)
    storage.fail_source_delete = True
    with pytest.raises(CleanupRetryableError, match="retried"):
        worker.cleanup_source_after_commit(result)
    assert storage.deleted == []


def test_worker_scans_original_and_normalized_and_rejects_either_before_put() -> None:
    payload = valid_pdf()
    job = make_job(payload)
    source = stored("quarantine", job.source_key, "source-v1", payload, job)
    storage = FakeStorage(source, payload)
    scanner = RecordingScanner(reject_call=2)
    with pytest.raises(ScanRejected, match="normalized"):
        local_worker(storage, scanner).process(job)
    assert len(scanner.payloads) == 2
    assert scanner.payloads[0] == payload
    assert scanner.payloads[1] != payload
    assert storage.put_key is None


def test_original_scan_rejection_happens_before_pdf_parser() -> None:
    payload = b"%PDF-1.7\nmalicious-unparseable-fixture\n%%EOF\n"
    job = make_job(payload)
    source = stored("quarantine", job.source_key, "source-v1", payload, job)
    scanner = RecordingScanner(reject_call=1)
    with pytest.raises(ScanRejected, match="original"):
        local_worker(FakeStorage(source, payload), scanner).process(job)
    assert scanner.payloads == [payload]


def test_job_rejects_same_or_traversing_storage_keys() -> None:
    payload = valid_pdf()
    job = make_job(payload)
    with pytest.raises(ScanRejected, match="distinct"):
        local_worker(
            FakeStorage(stored("quarantine", job.source_key, "source-v1", payload, job), payload),
            DeterministicLocalScanner(),
        ).process(VerifyPdfJob(**{**job.__dict__, "ready_key": job.source_key}))
    with pytest.raises(ScanRejected, match="invalid"):
        local_worker(
            FakeStorage(stored("quarantine", job.source_key, "source-v1", payload, job), payload),
            DeterministicLocalScanner(),
        ).process(VerifyPdfJob(**{**job.__dict__, "source_key": "../escape"}))
    other_tenant_ready = f"tenants/{uuid4()}/assets/{uuid4()}/v1/{uuid4()}"
    with pytest.raises(ScanRejected, match="tenant scope"):
        local_worker(
            FakeStorage(stored("quarantine", job.source_key, "source-v1", payload, job), payload),
            DeterministicLocalScanner(),
        ).process(VerifyPdfJob(**{**job.__dict__, "ready_key": other_tenant_ready}))
    with pytest.raises(ScanRejected, match="buckets"):
        local_worker(
            FakeStorage(stored("quarantine", job.source_key, "source-v1", payload, job), payload),
            DeterministicLocalScanner(),
        ).process(VerifyPdfJob(**{**job.__dict__, "source_bucket": "wrong"}))


def test_remote_readiness_rejects_deterministic_scanner() -> None:
    storage: Any = SimpleNamespace(assert_runtime_controls=lambda **_kwargs: None)
    with pytest.raises(StorageCapabilityError, match="production-capable"):
        assert_storage_worker_ready(
            environment="production",
            storage=storage,
            scanner=DeterministicLocalScanner(),
            allowed_origin="https://sell.autonomia.site",
        )


class FakeStorage:
    quarantine_bucket = "quarantine"
    ready_bucket = "ready"

    def __init__(self, source: StoredObject, payload: bytes) -> None:
        self.source = source
        self.payload = payload
        self.ready_payload = b""
        self.page_payloads: dict[str, bytes] = {}
        self.put_key: str | None = None
        self.deleted: list[tuple[str, str, str]] = []
        self.reconciled_prefixes: list[tuple[str, str]] = []
        self.fail_source_delete = False

    def head(self, **_kwargs: Any) -> StoredObject:
        return self.source

    def open_download(self, **_kwargs: Any) -> Any:
        return io.BytesIO(self.payload)

    def put_verified(
        self,
        *,
        destination_bucket: str,
        destination_key: str,
        source_path: Path,
        byte_size: int,
        sha256_hex: str,
        content_type: str,
        upload_id: str,
        file_name: str,
    ) -> StoredObject:
        suffixes = {
            "application/pdf": ".pdf",
            "video/mp4": ".mp4",
            "image/png": ".png",
        }
        assert file_name.endswith(suffixes[content_type])
        payload = source_path.read_bytes()
        assert len(payload) == byte_size
        assert hashlib.sha256(payload).hexdigest() == sha256_hex
        if content_type == "image/png":
            self.page_payloads[destination_key] = payload
            version_id = "page-v1"
        else:
            self.put_key = destination_key
            self.ready_payload = payload
            version_id = "ready-v1"
        return StoredObject(
            bucket=destination_bucket,
            key=destination_key,
            version_id=version_id,
            size=byte_size,
            content_type=content_type,
            checksum_sha256_b64=base64.b64encode(bytes.fromhex(sha256_hex)).decode(),
            etag="ready-etag",
            appsell_upload_id=upload_id,
        )

    def delete_version(self, *, bucket: str, key: str, version_id: str) -> None:
        if bucket == self.quarantine_bucket and self.fail_source_delete:
            raise RuntimeError("injected source delete failure")
        self.deleted.append((bucket, key, version_id))

    def delete_all_versions_under_prefix(self, *, bucket: str, prefix: str) -> int:
        self.reconciled_prefixes.append((bucket, prefix))
        return 0


class RecordingScanner:
    provider_name = "recording-test"
    production_capable = False

    def __init__(self, *, reject_call: int, events: list[str] | None = None) -> None:
        self.reject_call = reject_call
        self.payloads: list[bytes] = []
        self.events = events

    def healthy(self) -> bool:
        return True

    def scan(self, path: Path) -> bool:
        if self.events is not None:
            self.events.append("scan")
        self.payloads.append(path.read_bytes())
        return len(self.payloads) != self.reject_call


@pytest.mark.parametrize(
    ("response", "expected"),
    [(b"stream: OK\0", True), (b"stream: Eicar-Test-Signature FOUND\0", False)],
)
def test_clamav_scanner_uses_bounded_unix_instream_protocol(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    response: bytes,
    expected: bool,
) -> None:
    sent: list[bytes] = []

    class FakeSocket:
        def __enter__(self) -> FakeSocket:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def settimeout(self, value: float) -> None:
            assert value == 3.0

        def connect(self, path: str) -> None:
            assert path == "/run/appsell-clamav/clamd.sock"

        def sendall(self, payload: bytes) -> None:
            sent.append(payload)

        def recv(self, _size: int) -> bytes:
            return response

    monkeypatch.setattr("appsell_api.storage_worker.socket.socket", lambda *_args: FakeSocket())
    source = tmp_path / "material.pdf"
    source.write_bytes(b"safe-pdf")

    assert ClamAvScanner("/run/appsell-clamav/clamd.sock").scan(source) is expected
    assert sent[0] == b"zINSTREAM\0"
    assert sent[-1] == b"\0\0\0\0"
    assert b"safe-pdf" in sent


def test_clamav_invalid_response_is_retryable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeSocket:
        def __enter__(self) -> FakeSocket:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def settimeout(self, _value: float) -> None:
            pass

        def connect(self, _path: str) -> None:
            pass

        def sendall(self, _payload: bytes) -> None:
            pass

        def recv(self, _size: int) -> bytes:
            return b"stream: ERROR\0"

    monkeypatch.setattr("appsell_api.storage_worker.socket.socket", lambda *_args: FakeSocket())
    source = tmp_path / "material.pdf"
    source.write_bytes(b"safe-pdf")

    with pytest.raises(ScannerUnavailable):
        ClamAvScanner("/run/appsell-clamav/clamd.sock").scan(source)


def test_clamav_health_requires_fresh_signatures(monkeypatch: pytest.MonkeyPatch) -> None:
    fresh = datetime.now(UTC).strftime("%a %b %d %H:%M:%S %Y")
    responses = iter((b"PONG\0", f"ClamAV 1.4/123/{fresh}\0".encode()))

    class FakeSocket:
        def __enter__(self) -> FakeSocket:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def settimeout(self, _value: float) -> None:
            pass

        def connect(self, _path: str) -> None:
            pass

        def sendall(self, _payload: bytes) -> None:
            pass

        def recv(self, _size: int) -> bytes:
            return next(responses)

    monkeypatch.setattr("appsell_api.storage_worker.socket.socket", lambda *_args: FakeSocket())

    assert ClamAvScanner("/run/appsell-clamav/clamd.sock").healthy() is True


def make_job(
    payload: bytes,
    *,
    media_type: str = "application/pdf",
    file_name: str = "material.pdf",
) -> VerifyPdfJob:
    upload_id = uuid4()
    return VerifyPdfJob(
        seller_id=(seller_id := uuid4()),
        upload_id=upload_id,
        source_bucket="quarantine",
        ready_bucket="ready",
        source_key=f"tenants/{seller_id}/quarantine/{upload_id}",
        ready_key=f"tenants/{seller_id}/assets/{uuid4()}/v1/{uuid4()}",
        source_version_id="source-v1",
        expected_size=len(payload),
        expected_sha256_hex=hashlib.sha256(payload).hexdigest(),
        declared_content_type=media_type,
        file_name=file_name,
    )


def stored(
    bucket: str, key: str, version_id: str, payload: bytes, job: VerifyPdfJob
) -> StoredObject:
    return StoredObject(
        bucket=bucket,
        key=key,
        version_id=version_id,
        size=len(payload),
        content_type=job.declared_content_type,
        checksum_sha256_b64=base64.b64encode(hashlib.sha256(payload).digest()).decode(),
        etag="etag",
        appsell_upload_id=str(job.upload_id),
    )


def valid_pdf() -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(320, 480), invariant=1)
    canvas.drawString(32, 430, "Verified")
    canvas.showPage()
    canvas.save()
    return output.getvalue()


def valid_mp4() -> bytes:
    def mp4_box(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I4s", len(payload) + 8, kind) + payload

    return (
        mp4_box(b"ftyp", b"isom\0\0\0\0isommp42")
        + mp4_box(b"moov", b"metadata")
        + mp4_box(b"mdat", b"video")
    )
