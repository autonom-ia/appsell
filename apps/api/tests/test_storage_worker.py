from __future__ import annotations

import base64
import hashlib
import io
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
    assert storage.deleted == []
    worker = local_worker(storage, DeterministicLocalScanner())
    worker.cleanup_source_after_commit(result)
    assert storage.deleted == [("quarantine", job.source_key, "source-v1")]


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
    assert storage.deleted == [("ready", job.ready_key, "ready-v1")]


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
        self.put_key: str | None = None
        self.deleted: list[tuple[str, str, str]] = []
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
        assert file_name == "material.pdf"
        self.put_key = destination_key
        self.ready_payload = source_path.read_bytes()
        assert len(self.ready_payload) == byte_size
        assert hashlib.sha256(self.ready_payload).hexdigest() == sha256_hex
        return StoredObject(
            bucket=destination_bucket,
            key=destination_key,
            version_id="ready-v1",
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


class RecordingScanner:
    provider_name = "recording-test"
    production_capable = False

    def __init__(self, *, reject_call: int) -> None:
        self.reject_call = reject_call
        self.payloads: list[bytes] = []

    def healthy(self) -> bool:
        return True

    def scan(self, path: Path) -> bool:
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


def make_job(payload: bytes) -> VerifyPdfJob:
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
    )


def stored(
    bucket: str, key: str, version_id: str, payload: bytes, job: VerifyPdfJob
) -> StoredObject:
    return StoredObject(
        bucket=bucket,
        key=key,
        version_id=version_id,
        size=len(payload),
        content_type="application/pdf",
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
