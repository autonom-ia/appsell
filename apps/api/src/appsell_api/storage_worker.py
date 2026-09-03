from __future__ import annotations

import shutil
import socket
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Literal, Protocol
from uuid import UUID

from .asset_media import is_document
from .document_verifier import spooled_document_file
from .image_verifier import ImageRejected, spooled_cover_file
from .logging_setup import get_logger
from .pdf_derivatives import prepared_pdf_file
from .pdf_verifier import PDF_MIME, spooled_pdf_file
from .storage_s3 import (
    S3Storage,
    StorageCapabilityError,
    StoredObject,
    needs_multipart,
    object_sha256_matches,
)
from .video_verifier import VIDEO_MP4_MIME, prepared_catalogue_video, spooled_video_file

_EICAR = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"
_logger = get_logger(__name__)


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
class VerifiedStoredPdfPage:
    page_number: int
    stored: StoredObject
    byte_size: int
    sha256_hex: str
    width: int
    height: int

    def manifest_entry(self) -> dict[str, object]:
        return {
            "page_number": self.page_number,
            "storage_driver": "s3",
            "storage_bucket": self.stored.bucket,
            "object_key": self.stored.key,
            "storage_version_id": self.stored.version_id,
            "storage_etag": self.stored.etag,
            "byte_size": self.byte_size,
            "sha256": self.sha256_hex,
            "pixel_width": self.width,
            "pixel_height": self.height,
        }


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
    pdf_pages: tuple[VerifiedStoredPdfPage, ...] = ()


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
        # Um envio fatiado não carrega o hash do arquivo inteiro: o storage guarda
        # um hash composto das partes, porque o protocolo só calcula hash de
        # arquivo inteiro em envio fatiado com algoritmos CRC, e SHA-256 não é um
        # deles. Para esses, a conferência é a releitura abaixo, que passa por
        # todos os bytes e compara com este mesmo `expected_sha256_hex`. Nada é
        # promovido sem o hash bater; muda onde a comparação acontece.
        if not needs_multipart(job.expected_size) and not object_sha256_matches(
            source, job.expected_sha256_hex
        ):
            raise ScanRejected("quarantine object checksum differs from upload intent")
        if source.appsell_upload_id != str(job.upload_id):
            raise ScanRejected("quarantine upload metadata differs from upload intent")

        if job.declared_content_type == VIDEO_MP4_MIME:
            return self._process_video(job, source)
        if is_document(job.declared_content_type):
            return self._process_document(job, source)
        if job.declared_content_type != PDF_MIME:
            raise ScanRejected("job MIME is unsupported")

        uploaded: list[StoredObject] = []
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
                    with prepared_pdf_file(
                        source_pdf.path,
                        isolated_service_boundary=self.isolated_service_boundary,
                    ) as prepared:
                        if not self.scanner.scan(prepared.ready_pdf_path):
                            raise ScanRejected("scanner rejected the normalized ready object")
                        # The asset version prefix is reserved exclusively for this upload.
                        # Reconcile any versions left by an abruptly terminated prior lease
                        # before writing a fresh, internally consistent derivative set.
                        self.storage.delete_all_versions_under_prefix(
                            bucket=job.ready_bucket,
                            prefix=ready_asset_version_prefix(job.ready_key, job.seller_id),
                        )
                        ready = self.storage.put_verified(
                            destination_bucket=job.ready_bucket,
                            destination_key=job.ready_key,
                            source_path=prepared.ready_pdf_path,
                            byte_size=prepared.ready_pdf_byte_size,
                            sha256_hex=prepared.ready_pdf_sha256_hex,
                            content_type=PDF_MIME,
                            upload_id=str(job.upload_id),
                            file_name=job.file_name,
                        )
                        uploaded.append(ready)
                        stored_pages: list[VerifiedStoredPdfPage] = []
                        for page in prepared.pages:
                            stored_page = self.storage.put_verified(
                                destination_bucket=job.ready_bucket,
                                destination_key=_pdf_page_key(job.ready_key, page.page_number),
                                source_path=page.path,
                                byte_size=page.byte_size,
                                sha256_hex=page.sha256_hex,
                                content_type="image/png",
                                upload_id=str(job.upload_id),
                                file_name=f"page-{page.page_number}.png",
                            )
                            uploaded.append(stored_page)
                            stored_pages.append(
                                VerifiedStoredPdfPage(
                                    page_number=page.page_number,
                                    stored=stored_page,
                                    byte_size=page.byte_size,
                                    sha256_hex=page.sha256_hex,
                                    width=page.width,
                                    height=page.height,
                                )
                            )
                        return VerifiedAssetResult(
                            source=source,
                            ready=ready,
                            source_byte_size=source_pdf.byte_size,
                            source_sha256_hex=source_pdf.sha256_hex,
                            ready_byte_size=prepared.ready_pdf_byte_size,
                            ready_sha256_hex=prepared.ready_pdf_sha256_hex,
                            pdf_page_count=prepared.page_count,
                            pdf_pages=tuple(stored_pages),
                            scanner_provider=self.scanner.provider_name,
                        )
        except Exception as processing_error:
            if not uploaded:
                raise
            cleanup_errors = _delete_versions(self.storage, reversed(uploaded))
            if cleanup_errors:
                raise ExceptionGroup(
                    "processing and PDF derivative compensation failed",
                    [processing_error, *cleanup_errors],
                ) from processing_error
            raise

    def _process_document(self, job: VerifyPdfJob, source: StoredObject) -> VerifiedAssetResult:
        """Promote a document only after proving it is what it claims to be.

        The bytes are stored unchanged, exactly as the video branch does, because
        a document is handed over as a download and never rendered. What must not
        be trusted is the extension: the verifier opens the container and refuses
        a macro project whatever the file is called.
        """
        with self.storage.open_download(
            bucket=source.bucket, key=source.key, version_id=source.version_id
        ) as stream:
            with spooled_document_file(
                stream,
                expected_size=job.expected_size,
                expected_sha256_hex=job.expected_sha256_hex,
                declared_content_type=job.declared_content_type,
            ) as document:
                if not self.scanner.scan(document.path):
                    raise ScanRejected("scanner rejected the quarantined document")
                ready = self.storage.put_verified(
                    destination_bucket=job.ready_bucket,
                    destination_key=job.ready_key,
                    source_path=document.path,
                    byte_size=document.byte_size,
                    sha256_hex=document.sha256_hex,
                    content_type=job.declared_content_type,
                    upload_id=str(job.upload_id),
                    file_name=job.file_name,
                )
                return VerifiedAssetResult(
                    source=source,
                    ready=ready,
                    source_byte_size=document.byte_size,
                    source_sha256_hex=document.sha256_hex,
                    ready_byte_size=document.byte_size,
                    ready_sha256_hex=document.sha256_hex,
                    pdf_page_count=0,
                    scanner_provider=self.scanner.provider_name,
                )

    def _process_video(self, job: VerifyPdfJob, source: StoredObject) -> VerifiedAssetResult:
        """Promove a aula depois de trocar o invólucro por um que o aluno navega.

        O que é guardado não são os bytes que chegaram: é o derivado com
        `+faststart`, o índice do MP4 no começo do arquivo. Sem ele o navegador
        precisa achar o índice no fim antes de desenhar o primeiro quadro, e a
        barra de progresso não responde.

        A conversão é feita pelo serviço isolado, e os dois arquivos passam pelo
        scanner, como já acontece no ramo do PDF.
        """
        ready: StoredObject | None = None
        try:
            with self.storage.open_download(
                bucket=source.bucket, key=source.key, version_id=source.version_id
            ) as stream:
                with spooled_video_file(
                    stream,
                    expected_size=job.expected_size,
                    expected_sha256_hex=job.expected_sha256_hex,
                    declared_content_type=job.declared_content_type,
                ) as source_video:
                    if not self.scanner.scan(source_video.path):
                        raise ScanRejected("scanner rejected the quarantined video")
                    started = monotonic()
                    with prepared_catalogue_video(source_video.path) as prepared:
                        if not self.scanner.scan(prepared.path):
                            raise ScanRejected("scanner rejected the prepared video")
                        _logger.info(
                            "appsell_catalogue_video_prepared",
                            upload_id=str(job.upload_id),
                            source_byte_size=source_video.byte_size,
                            ready_byte_size=prepared.byte_size,
                            sandbox_attempts=prepared.attempts,
                            elapsed_ms=round((monotonic() - started) * 1000),
                        )
                        ready = self.storage.put_verified(
                            destination_bucket=job.ready_bucket,
                            destination_key=job.ready_key,
                            source_path=prepared.path,
                            byte_size=prepared.byte_size,
                            sha256_hex=prepared.sha256_hex,
                            content_type=VIDEO_MP4_MIME,
                            upload_id=str(job.upload_id),
                            file_name=job.file_name,
                        )
                        return VerifiedAssetResult(
                            source=source,
                            ready=ready,
                            source_byte_size=source_video.byte_size,
                            source_sha256_hex=source_video.sha256_hex,
                            ready_byte_size=prepared.byte_size,
                            ready_sha256_hex=prepared.sha256_hex,
                            pdf_page_count=0,
                            pdf_pages=(),
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
                    "video processing and ready compensation both failed",
                    [processing_error, ready_cleanup_error],
                ) from processing_error
            raise

    def compensate_ready_after_finalize_failure(self, result: VerifiedAssetResult) -> None:
        errors = _delete_versions(
            self.storage,
            [page.stored for page in reversed(result.pdf_pages)] + [result.ready],
        )
        if errors:
            raise CleanupRetryableError(
                "ready derivative cleanup must be retried"
            ) from ExceptionGroup("ready derivative cleanup failed", errors)

    def cleanup_source_after_commit(self, result: VerifiedAssetResult) -> None:
        try:
            self.storage.delete_version(
                bucket=result.source.bucket,
                key=result.source.key,
                version_id=result.source.version_id,
            )
        except Exception as error:
            raise CleanupRetryableError("quarantine cleanup must be retried") from error


@dataclass(frozen=True)
class VerifyCoverJob:
    """Uma imagem a verificar: a capa de um produto ou o logo de uma loja.

    ``product_id`` diz qual das duas. Com produto, as chaves esperadas são as
    da capa; sem produto (``None``), as do logo da loja (0052). O resto do
    trabalho — cabeçalho contra o intent, scanner, reescrita, promoção,
    compensação — é idêntico, e é por isso que o job é um só.
    """

    seller_id: UUID
    product_id: UUID | None
    upload_id: UUID
    source_bucket: str
    ready_bucket: str
    source_key: str
    ready_key: str
    expected_size: int
    expected_sha256_hex: str
    declared_content_type: str
    # Só vale sem produto: qual imagem da loja é ("logo" ou "banner", 0054).
    kind: str = "logo"


@dataclass(frozen=True)
class VerifiedCoverResult:
    source: StoredObject
    ready: StoredObject
    media_type: str
    byte_size: int
    sha256_hex: str
    width: int
    height: int
    scanner_provider: str


class ProductCoverWorker:
    """Promove a capa de um produto da quarentena para o bucket servido.

    Segue o mesmo desenho do material de aula — cabeçalho conferido contra o
    intent, scanner antes de qualquer promoção, e compensação se a etapa
    seguinte falhar — mas com um caminho próprio, porque a capa não é material:
    ela é reescrita para perder metadados, é sempre exibida em vez de baixada, e
    o digest que sobrevive é o do arquivo reescrito, não o do que subiu.
    """

    def __init__(self, storage: S3Storage, scanner: MalwareScanner) -> None:
        self.storage = storage
        self.scanner = scanner

    def process(self, job: VerifyCoverJob) -> VerifiedCoverResult:
        _validate_storage_key(job.source_key)
        _validate_storage_key(job.ready_key)
        if (
            job.source_bucket != self.storage.quarantine_bucket
            or job.ready_bucket != self.storage.ready_bucket
        ):
            raise ScanRejected("job buckets do not match configured storage boundaries")
        expected_source, expected_ready = expected_image_keys(job)
        if job.source_key != expected_source:
            raise ScanRejected("cover quarantine key is outside the job tenant scope")
        if job.ready_key != expected_ready:
            raise ScanRejected("cover ready key is outside the job tenant scope")
        if not self.scanner.healthy():
            raise ScannerUnavailable("malware scanner health check failed")

        source = self.storage.head(bucket=job.source_bucket, key=job.source_key)
        if source.size != job.expected_size:
            raise ScanRejected("quarantine cover size differs from upload intent")
        if source.content_type != job.declared_content_type:
            raise ScanRejected("quarantine cover MIME differs from upload intent")
        if not object_sha256_matches(source, job.expected_sha256_hex):
            raise ScanRejected("quarantine cover checksum differs from upload intent")
        # A quarentena da capa tem chave por produto, não por upload: a segunda
        # tentativa sobrescreve a primeira em vez de acumular lixo. É este
        # cabeçalho que diz qual das tentativas está ali agora.
        if source.appsell_upload_id != str(job.upload_id):
            raise ScanRejected("quarantine cover belongs to another upload attempt")

        ready: StoredObject | None = None
        try:
            with self.storage.open_download(
                bucket=source.bucket, key=source.key, version_id=source.version_id
            ) as stream:
                with spooled_cover_file(
                    stream,
                    expected_size=job.expected_size,
                    expected_sha256_hex=job.expected_sha256_hex,
                    declared_content_type=job.declared_content_type,
                ) as cover:
                    if not self.scanner.scan(cover.path):
                        raise ScanRejected("scanner rejected the quarantined cover")
                    ready = self.storage.put_cover(
                        destination_key=job.ready_key,
                        source_path=cover.path,
                        byte_size=cover.byte_size,
                        sha256_hex=cover.sha256_hex,
                        content_type=cover.media_type,
                    )
                    return VerifiedCoverResult(
                        source=source,
                        ready=ready,
                        media_type=cover.media_type,
                        byte_size=cover.byte_size,
                        sha256_hex=cover.sha256_hex,
                        width=cover.width,
                        height=cover.height,
                        scanner_provider=self.scanner.provider_name,
                    )
        except ImageRejected as rejection:
            raise ScanRejected(str(rejection)) from rejection
        except Exception as processing_error:
            if ready is None:
                raise
            errors = _delete_versions(self.storage, [ready])
            if errors:
                raise ExceptionGroup(
                    "cover processing and ready compensation both failed",
                    [processing_error, *errors],
                ) from processing_error
            raise

    def discard_ready_cover(self, result: VerifiedCoverResult) -> None:
        """Desfaz a promoção quando a gravação no banco não aconteceu."""
        if _delete_versions(self.storage, [result.ready]):
            raise CleanupRetryableError("ready cover cleanup must be retried")

    def cleanup_source_after_commit(self, result: VerifiedCoverResult) -> None:
        """Apaga a tentativa da quarentena, todas as versões dela.

        Todas, e não a que foi lida: a chave é por produto, então tentativas
        anteriores do mesmo produto deixaram versões atrás desta. Apagar só uma
        deixaria o resto para sempre.
        """
        try:
            self.storage.delete_all_versions(bucket=result.source.bucket, key=result.source.key)
        except Exception as error:
            raise CleanupRetryableError("cover quarantine cleanup must be retried") from error

    def discard_abandoned_cover(self, *, seller_id: UUID, product_id: UUID) -> None:
        """Apaga a quarentena de uma tentativa que não virou capa."""
        try:
            self.storage.delete_all_versions(
                bucket=self.storage.quarantine_bucket,
                key=cover_quarantine_key(seller_id, product_id),
            )
        except Exception as error:
            raise CleanupRetryableError("cover quarantine cleanup must be retried") from error

    def discard_abandoned_image(self, *, seller_id: UUID, kind: str) -> None:
        """Apaga a quarentena de uma tentativa que não virou logo nem banner."""
        try:
            self.storage.delete_all_versions(
                bucket=self.storage.quarantine_bucket,
                key=image_quarantine_key(seller_id, kind),
            )
        except Exception as error:
            raise CleanupRetryableError("image quarantine cleanup must be retried") from error

    def discard_abandoned_logo(self, *, seller_id: UUID) -> None:
        self.discard_abandoned_image(seller_id=seller_id, kind="logo")


def expected_image_keys(job: VerifyCoverJob) -> tuple[str, str]:
    """As chaves que este job tem o direito de tocar, e nenhuma outra."""
    if job.product_id is None:
        return (
            image_quarantine_key(job.seller_id, job.kind),
            image_ready_key(job.seller_id, job.kind, job.upload_id),
        )
    return (
        cover_quarantine_key(job.seller_id, job.product_id),
        cover_ready_key(job.seller_id, job.product_id, job.upload_id),
    )


SELLER_IMAGE_KINDS = frozenset({"logo", "banner"})


def image_quarantine_key(seller_id: UUID, kind: str) -> str:
    if kind not in SELLER_IMAGE_KINDS:
        raise ScanRejected("unknown seller image kind")
    return f"tenants/{seller_id}/quarantine/{kind}"


def image_ready_key(seller_id: UUID, kind: str, upload_id: UUID) -> str:
    if kind not in SELLER_IMAGE_KINDS:
        raise ScanRejected("unknown seller image kind")
    return f"tenants/{seller_id}/{kind}/{upload_id}"


def logo_quarantine_key(seller_id: UUID) -> str:
    return image_quarantine_key(seller_id, "logo")


def logo_ready_key(seller_id: UUID, upload_id: UUID) -> str:
    return image_ready_key(seller_id, "logo", upload_id)


def cover_quarantine_key(seller_id: UUID, product_id: UUID) -> str:
    return f"tenants/{seller_id}/quarantine/cover/{product_id}"


def cover_ready_key(seller_id: UUID, product_id: UUID, upload_id: UUID) -> str:
    return f"tenants/{seller_id}/covers/{product_id}/{upload_id}"


def assert_storage_worker_ready(
    *,
    environment: str,
    storage: S3Storage,
    scanner: MalwareScanner,
    allowed_origin: str,
    cors_mode: Literal["s3", "edge"] = "s3",
) -> None:
    storage.assert_runtime_controls(
        allowed_origin=allowed_origin,
        require_tls=environment in {"staging", "production"},
        cors_mode=cors_mode,
    )
    if not scanner.healthy():
        raise StorageCapabilityError("malware scanner is unhealthy")
    if environment in {"staging", "production"} and not scanner.production_capable:
        raise StorageCapabilityError(
            "production-capable malware scanner is required outside local/CI"
        )

    # O worker chama pdftoppm e ffmpeg como processos. Faltando, ele pega o
    # trabalho, falha no meio, engole o erro e repete ate o lease expirar — sem
    # nada dizer que a ferramenta nao existe. Recusar subir e alto e imediato.
    for tool in ("pdftoppm", "ffmpeg"):
        if shutil.which(tool) is None:
            raise StorageCapabilityError(
                f"{tool} is unavailable: the asset worker cannot process media without it"
            )

    # Aplicada na subida, e não exigida: um ambiente cuja credencial não pode
    # escrever política de bucket não deve ficar sem worker por causa disso. O
    # cancelamento continua abortando os envios, e o que esta regra cobre é só o
    # vendedor que fechou a aba. Falhar aqui é registrado com o que fazer.
    try:
        storage.ensure_multipart_expiry()
    except Exception as error:  # noqa: BLE001 - qualquer falha aqui é a mesma decisão
        _logger.warning(
            "appsell_multipart_expiry_not_applied",
            bucket=storage.quarantine_bucket,
            reason=type(error).__name__,
            remediation="aplicar AbortIncompleteMultipartUpload no bucket de quarentena",
        )


def _validate_storage_key(key: str) -> None:
    invalid_part = any(part in {"", ".", ".."} for part in key.split("/"))
    if not key or key.startswith("/") or "\0" in key or invalid_part:
        raise ScanRejected("storage key is invalid")


def _pdf_page_key(ready_key: str, page_number: int) -> str:
    parts = ready_key.split("/")
    try:
        UUID(parts[1])
        UUID(parts[3])
        UUID(parts[5])
        version = int(parts[4][1:])
    except (IndexError, ValueError) as error:
        raise ScanRejected("ready PDF key does not identify an asset version") from error
    if len(parts) != 6 or parts[0] != "tenants" or parts[2] != "assets" or version < 1:
        raise ScanRejected("ready PDF key does not identify an asset version")
    return "/".join([*parts[:5], "pages", f"{page_number}.png"])


def ready_asset_version_prefix(ready_key: str, seller_id: UUID) -> str:
    # Reuse the strict tenant/asset/version/blob validation before deriving a
    # prefix that can be passed to a destructive storage reconciliation call.
    page_key = _pdf_page_key(ready_key, 1)
    if UUID(ready_key.split("/")[1]) != seller_id:
        raise ScanRejected("ready PDF key is outside the cleanup tenant scope")
    return page_key.rsplit("pages/1.png", 1)[0]


def _delete_versions(storage: S3Storage, objects: Iterable[StoredObject]) -> list[Exception]:
    errors: list[Exception] = []
    for stored in objects:
        try:
            storage.delete_version(
                bucket=stored.bucket, key=stored.key, version_id=stored.version_id
            )
        except Exception as error:
            errors.append(error)
    return errors
