from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID

from .pdf_verifier import PDF_MIME, normalized_pdf_file, spooled_pdf_file
from .storage_s3 import S3Storage, StorageCapabilityError, StoredObject, object_sha256_matches

_EICAR = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"


class ScanRejected(RuntimeError):
    pass


class CleanupRetryableError(RuntimeError):
    pass


class ScannerUnavailable(RuntimeError):
    pass


class MalwareScanner(Protocol):
    provider_name: str
    production_capable: bool

    def healthy(self) -> bool: ...

    def scan(self, path: Path) -> bool: ...


class DeterministicLocalScanner:
    """Local/CI structural gate; explicitly not a commercial malware scanner."""

    provider_name = "deterministic-local"
    production_capable = False

    def healthy(self) -> bool:
        return True

    def scan(self, path: Path) -> bool:
        with path.open("rb") as source:
            overlap = b""
            while chunk := source.read(64 * 1024):
                candidate = overlap + chunk
                if _EICAR in candidate:
                    return False
                overlap = candidate[-len(_EICAR) :]
        return True


class ClamAvScanner:
    """Bounded clamd INSTREAM adapter over a local Unix socket."""

    provider_name = "clamav-clamd"
    production_capable = True

    def __init__(self, socket_path: str, *, timeout_seconds: float = 3.0) -> None:
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds

    def healthy(self) -> bool:
        try:
            if self._request(b"zPING\0", stream=None) != b"PONG":
                return False
            version = self._request(b"zVERSION\0", stream=None).decode("ascii")
            signature_date = datetime.strptime(
                version.rsplit("/", 1)[1], "%a %b %d %H:%M:%S %Y"
            ).replace(tzinfo=UTC)
            age = datetime.now(UTC) - signature_date
            return -timedelta(hours=1) <= age <= timedelta(hours=48)
        except (IndexError, UnicodeDecodeError, ValueError, ScannerUnavailable):
            return False

    def scan(self, path: Path) -> bool:
        response = self._request(b"zINSTREAM\0", stream=path)
        if response.endswith(b" OK"):
            return True
        if response.endswith(b" FOUND"):
            return False
        raise ScannerUnavailable("malware scanner returned an invalid response")

    def _request(self, command: bytes, *, stream: Path | None) -> bytes:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(self.timeout_seconds)
                client.connect(self.socket_path)
                client.sendall(command)
                if stream is not None:
                    with stream.open("rb") as source:
                        while chunk := source.read(64 * 1024):
                            client.sendall(struct.pack("!I", len(chunk)))
                            client.sendall(chunk)
                    client.sendall(struct.pack("!I", 0))
                response = bytearray()
                while len(response) <= 4_096:
                    chunk = client.recv(4_096 - len(response))
                    if not chunk:
                        break
                    response.extend(chunk)
                    if b"\0" in chunk:
                        break
        except (OSError, ValueError) as error:
            raise ScannerUnavailable("malware scanner is unavailable") from error
        if not response or len(response) > 4_096 or b"\0" not in response:
            raise ScannerUnavailable("malware scanner returned an invalid response")
        return bytes(response).split(b"\0", 1)[0]


@dataclass(frozen=True)
class VerifyPdfJob:
    seller_id: UUID
    upload_id: UUID
    source_bucket: str
    ready_bucket: str
    source_key: str
    ready_key: str
    source_version_id: str
    expected_size: int
    expected_sha256_hex: str
    declared_content_type: str = PDF_MIME
    file_name: str = "material.pdf"


@dataclass(frozen=True)
class VerifiedAssetResult:
    source: StoredObject
    ready: StoredObject
    source_byte_size: int
    source_sha256_hex: str
    ready_byte_size: int
    ready_sha256_hex: str
    pdf_page_count: int
    scanner_provider: str


class PdfStorageWorker:
    def __init__(
        self,
        storage: S3Storage,
        scanner: MalwareScanner,
        *,
        isolated_service_boundary: bool = False,
    ) -> None:
        self.storage = storage
        self.scanner = scanner
        self.isolated_service_boundary = isolated_service_boundary

    def process(self, job: VerifyPdfJob) -> VerifiedAssetResult:
        _validate_storage_key(job.source_key)
        _validate_storage_key(job.ready_key)
        if (
            job.source_bucket != self.storage.quarantine_bucket
            or job.ready_bucket != self.storage.ready_bucket
        ):
            raise ScanRejected("job buckets do not match configured storage boundaries")
        if job.source_key == job.ready_key:
            raise ScanRejected("quarantine and ready keys must be distinct")
        expected_source_key = f"tenants/{job.seller_id}/quarantine/{job.upload_id}"
        expected_ready_prefix = f"tenants/{job.seller_id}/assets/"
        if job.source_key != expected_source_key or not job.ready_key.startswith(
            expected_ready_prefix
        ):
            raise ScanRejected("storage keys are outside the job tenant scope")
        if not self.scanner.healthy():
            raise ScannerUnavailable("malware scanner health check failed")
        source = self.storage.head(
            bucket=job.source_bucket,
            key=job.source_key,
            version_id=job.source_version_id,
        )
        if source.size != job.expected_size:
            raise ScanRejected("quarantine object size differs from upload intent")
        if source.content_type != job.declared_content_type:
            raise ScanRejected("quarantine object MIME differs from upload intent")
        if not object_sha256_matches(source, job.expected_sha256_hex):
            raise ScanRejected("quarantine object checksum differs from upload intent")
        if source.appsell_upload_id != str(job.upload_id):
            raise ScanRejected("quarantine upload metadata differs from upload intent")

        ready: StoredObject | None = None
        try:
            with self.storage.open_download(
                bucket=source.bucket, key=source.key, version_id=source.version_id
            ) as stream:
                with spooled_pdf_file(
                    stream,
                    expected_size=job.expected_size,
                    expected_sha256_hex=job.expected_sha256_hex,
                    declared_content_type=job.declared_content_type,
                ) as source_pdf:
                    if not self.scanner.scan(source_pdf.path):
                        raise ScanRejected("scanner rejected the original quarantined object")
                    with normalized_pdf_file(
                        source_pdf.path,
                        isolated_service_boundary=self.isolated_service_boundary,
                    ) as ready_pdf:
                        if not self.scanner.scan(ready_pdf.path):
                            raise ScanRejected("scanner rejected the normalized ready object")
                        ready = self.storage.put_verified(
                            destination_bucket=job.ready_bucket,
                            destination_key=job.ready_key,
                            source_path=ready_pdf.path,
                            byte_size=ready_pdf.byte_size,
                            sha256_hex=ready_pdf.sha256_hex,
                            content_type=PDF_MIME,
                            upload_id=str(job.upload_id),
                            file_name=job.file_name,
                        )
                        return VerifiedAssetResult(
                            source=source,
                            ready=ready,
                            source_byte_size=source_pdf.byte_size,
                            source_sha256_hex=source_pdf.sha256_hex,
                            ready_byte_size=ready_pdf.byte_size,
                            ready_sha256_hex=ready_pdf.sha256_hex,
                            pdf_page_count=ready_pdf.page_count,
                            scanner_provider=self.scanner.provider_name,
                        )
        except Exception as processing_error:
            if ready is None:
                raise
            try:
                self.storage.delete_version(
                    bucket=ready.bucket, key=ready.key, version_id=ready.version_id
                )
            except Exception as ready_cleanup_error:
                raise ExceptionGroup(
                    "processing and ready compensation both failed",
                    [processing_error, ready_cleanup_error],
                ) from processing_error
            raise

    def compensate_ready_after_finalize_failure(self, result: VerifiedAssetResult) -> None:
        self.storage.delete_version(
            bucket=result.ready.bucket,
            key=result.ready.key,
            version_id=result.ready.version_id,
        )

    def cleanup_source_after_commit(self, result: VerifiedAssetResult) -> None:
        try:
            self.storage.delete_version(
                bucket=result.source.bucket,
                key=result.source.key,
                version_id=result.source.version_id,
            )
        except Exception as error:
            raise CleanupRetryableError("quarantine cleanup must be retried") from error


def assert_storage_worker_ready(
    *, environment: str, storage: S3Storage, scanner: MalwareScanner, allowed_origin: str
) -> None:
    storage.assert_runtime_controls(
        allowed_origin=allowed_origin,
        require_tls=environment in {"staging", "production"},
    )
    if not scanner.healthy():
        raise StorageCapabilityError("malware scanner is unhealthy")
    if environment in {"staging", "production"} and not scanner.production_capable:
        raise StorageCapabilityError(
            "production-capable malware scanner is required outside local/CI"
        )


def _validate_storage_key(key: str) -> None:
    invalid_part = any(part in {"", ".", ".."} for part in key.split("/"))
    if not key or key.startswith("/") or "\0" in key or invalid_part:
        raise ScanRejected("storage key is invalid")
