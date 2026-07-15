from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import structlog
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .config import Settings
from .internal_requests import InternalRequestAuthenticator
from .pdf_verifier import PdfRejected, PdfSandboxUnavailable
from .service_auth import ServiceEnvelope, ServiceIdentity
from .storage_s3 import S3Storage, StorageError
from .storage_worker import (
    CleanupRetryableError,
    MalwareScanner,
    PdfStorageWorker,
    ScannerUnavailable,
    ScanRejected,
    VerifiedAssetResult,
    VerifyPdfJob,
)
from .video_verifier import VideoRejected, VideoSandboxUnavailable

_WORKER_SUBJECT = "asset-worker-v1"
_WORKER_LABEL = f"api-inline-{os.getpid()}"
logger = structlog.get_logger(__name__)


async def process_asset_upload(app: FastAPI, seller_id: UUID, upload_id: UUID) -> None:
    """Local/CI runner. Production requires a separately credentialed worker service."""
    settings: Settings = app.state.settings
    if settings.environment not in {"local", "ci"}:
        logger.error("asset_worker_inline_forbidden", upload_id=str(upload_id))
        return
    auth: InternalRequestAuthenticator | None = app.state.internal_auth
    storage: S3Storage | None = app.state.storage
    scanner: MalwareScanner | None = app.state.asset_scanner
    if auth is None or storage is None or scanner is None:
        logger.error("asset_worker_unavailable", upload_id=str(upload_id))
        return

    worker_id = _worker_id(settings)
    try:
        job_data = await _begin_job(auth, settings, worker_id, seller_id, upload_id)
    except (DBAPIError, RuntimeError):
        logger.warning("asset_worker_claim_failed", upload_id=str(upload_id))
        return

    await _process_claimed_job(
        app,
        auth,
        settings,
        worker_id,
        {**job_data, "seller_organization_id": seller_id, "upload_id": upload_id},
    )


async def run_asset_worker_loop(
    app: FastAPI, stop: asyncio.Event, *, allow_remote: bool = False
) -> None:
    """Durable consumer; remote environments require the dedicated worker entrypoint."""
    settings: Settings = app.state.settings
    if settings.environment not in {"local", "ci"} and not allow_remote:
        return
    auth: InternalRequestAuthenticator | None = app.state.internal_auth
    if auth is None:
        return
    worker_id = _worker_id(settings)
    while not stop.is_set():
        try:
            claimed = await _claim_jobs(auth, settings, worker_id)
            cleanup_claimed = await _claim_cleanup_jobs(auth, settings, worker_id)
            if claimed or cleanup_claimed:
                await asyncio.gather(
                    *(_process_claimed_job(app, auth, settings, worker_id, job) for job in claimed),
                    *(
                        _process_cleanup_job(app, auth, settings, worker_id, job)
                        for job in cleanup_claimed
                    ),
                )
                continue
        except (DBAPIError, RuntimeError, ValueError, KeyError):
            logger.warning("asset_worker_claim_loop_retry")
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
        except TimeoutError:
            pass


async def _claim_jobs(
    auth: InternalRequestAuthenticator,
    settings: Settings,
    worker_id: UUID,
) -> list[dict[str, Any]]:
    async with auth.trusted_domain_session(_worker_envelope(settings, worker_id)) as session:
        value = await session.scalar(
            text(
                "select app_private.claim_asset_verification_jobs("
                ":actor,:worker,:job_limit,:lease_seconds)"
            ),
            {
                "actor": worker_id,
                "worker": _WORKER_LABEL,
                "job_limit": 4,
                "lease_seconds": 300,
            },
        )
    if not isinstance(value, list) or any(not isinstance(job, dict) for job in value):
        raise RuntimeError("invalid asset worker claim response")
    return value


async def _claim_cleanup_jobs(
    auth: InternalRequestAuthenticator,
    settings: Settings,
    worker_id: UUID,
) -> list[dict[str, Any]]:
    async with auth.trusted_domain_session(_worker_envelope(settings, worker_id)) as session:
        value = await session.scalar(
            text(
                "select app_private.claim_asset_storage_cleanup_jobs("
                ":actor,:worker,:job_limit,:lease_seconds)"
            ),
            {
                "actor": worker_id,
                "worker": _WORKER_LABEL,
                "job_limit": 8,
                "lease_seconds": 300,
            },
        )
    if not isinstance(value, list) or any(not isinstance(job, dict) for job in value):
        raise RuntimeError("invalid asset cleanup claim response")
    return value


async def _process_cleanup_job(
    app: FastAPI,
    auth: InternalRequestAuthenticator,
    settings: Settings,
    worker_id: UUID,
    job: dict[str, Any],
) -> None:
    seller_id = UUID(str(job["seller_organization_id"]))
    cleanup_id = UUID(str(job["id"]))
    storage: S3Storage | None = app.state.storage
    if storage is None or str(job.get("storage_driver")) != "s3":
        await _retry_cleanup(
            auth, settings, worker_id, seller_id, cleanup_id, "storage_unavailable"
        )
        return
    try:
        if job.get("delete_mode") == "delete_all_versions":
            await _run_blocking(
                lambda cleanup: storage.delete_all_versions(
                    bucket=str(cleanup["bucket"]), key=str(cleanup["object_key"])
                ),
                job,
            )
        elif job.get("delete_mode") in {None, "delete_version"}:
            await _run_blocking(
                lambda cleanup: storage.delete_version(
                    bucket=str(cleanup["bucket"]),
                    key=str(cleanup["object_key"]),
                    version_id=str(cleanup["storage_version_id"]),
                ),
                job,
            )
        else:
            raise RuntimeError("invalid asset cleanup delete mode")
        await _complete_cleanup(auth, settings, worker_id, seller_id, cleanup_id)
    except (StorageError, OSError, DBAPIError):
        await _retry_cleanup(auth, settings, worker_id, seller_id, cleanup_id, "cleanup_retryable")
    except Exception:
        logger.exception("asset_cleanup_unexpected_retry", cleanup_id=str(cleanup_id))
        try:
            await _retry_cleanup(
                auth, settings, worker_id, seller_id, cleanup_id, "cleanup_unexpected"
            )
        except (DBAPIError, RuntimeError):
            logger.warning("asset_cleanup_retry_record_failed", cleanup_id=str(cleanup_id))


async def _complete_cleanup(
    auth: InternalRequestAuthenticator,
    settings: Settings,
    worker_id: UUID,
    seller_id: UUID,
    cleanup_id: UUID,
) -> None:
    async with auth.trusted_domain_session(_worker_envelope(settings, worker_id)) as session:
        await session.scalar(
            text("select app_private.complete_asset_storage_cleanup(:actor,:worker,:seller,:job)"),
            {
                "actor": worker_id,
                "worker": _WORKER_LABEL,
                "seller": seller_id,
                "job": cleanup_id,
            },
        )


async def _retry_cleanup(
    auth: InternalRequestAuthenticator,
    settings: Settings,
    worker_id: UUID,
    seller_id: UUID,
    cleanup_id: UUID,
    failure_code: str,
) -> None:
    async with auth.trusted_domain_session(_worker_envelope(settings, worker_id)) as session:
        await session.scalar(
            text(
                "select app_private.retry_asset_storage_cleanup("
                ":actor,:worker,:seller,:job,:delay_seconds,:failure_code)"
            ),
            {
                "actor": worker_id,
                "worker": _WORKER_LABEL,
                "seller": seller_id,
                "job": cleanup_id,
                "delay_seconds": 30,
                "failure_code": failure_code,
            },
        )


async def _process_claimed_job(
    app: FastAPI,
    auth: InternalRequestAuthenticator,
    settings: Settings,
    worker_id: UUID,
    job_data: dict[str, Any],
) -> None:
    storage: S3Storage | None = app.state.storage
    scanner: MalwareScanner | None = app.state.asset_scanner
    if storage is None or scanner is None:
        raise RuntimeError("asset worker dependencies are unavailable")
    seller_id = UUID(str(job_data.get("seller_organization_id") or job_data.get("seller_id")))
    upload_id = UUID(str(job_data["upload_id"]))

    worker = PdfStorageWorker(storage, scanner)
    job = VerifyPdfJob(
        seller_id=seller_id,
        upload_id=upload_id,
        source_bucket=str(job_data["quarantine_bucket"]),
        ready_bucket=str(job_data["ready_bucket"]),
        source_key=str(job_data["quarantine_key"]),
        ready_key=str(job_data["ready_key"]),
        source_version_id=str(job_data["source_version_id"]),
        expected_size=int(job_data["expected_byte_size"]),
        expected_sha256_hex=str(job_data["expected_sha256"]),
        declared_content_type=str(job_data["expected_media_type"]),
        file_name=str(job_data["file_name"]),
    )
    try:
        result = await _run_blocking(worker.process, job)
    except (PdfRejected, VideoRejected, ScanRejected) as error:
        await _reject_job(auth, settings, worker_id, seller_id, upload_id, job, error)
        return
    except (
        StorageError,
        PdfSandboxUnavailable,
        VideoSandboxUnavailable,
        ScannerUnavailable,
        OSError,
    ):
        # Leave the lease/source intact. The durable consumer can retry after lease expiry.
        logger.warning("asset_worker_storage_retry", upload_id=str(upload_id))
        return
    except Exception:
        # Parser/scanner compensation can surface an ExceptionGroup. One poisoned
        # upload must not terminate the durable consumer or sibling claims.
        logger.exception("asset_worker_unexpected_retry", upload_id=str(upload_id))
        return

    try:
        await _register_ready_candidate(auth, settings, worker_id, seller_id, upload_id, result)
    except DBAPIError:
        try:
            await _register_ready_candidate(auth, settings, worker_id, seller_id, upload_id, result)
        except DBAPIError:
            # No durable locator could be proven. The object is not referenced by
            # a publication yet, so exact-version compensation is safe.
            try:
                await _run_blocking(worker.compensate_ready_after_finalize_failure, result)
            except CleanupRetryableError:
                logger.error(
                    "asset_worker_ready_registration_and_cleanup_failed",
                    upload_id=str(upload_id),
                )
            return

    try:
        await _finalize_ready(auth, settings, worker_id, seller_id, upload_id, result)
    except DBAPIError:
        # A connection error during COMMIT is ambiguous. Replay the same receipt once;
        # never delete ready bytes unless the database proves they are unreferenced.
        try:
            await _finalize_ready(auth, settings, worker_id, seller_id, upload_id, result)
        except DBAPIError:
            logger.warning("asset_worker_finalize_reconcile", upload_id=str(upload_id))
            return

    try:
        await _run_blocking(worker.cleanup_source_after_commit, result)
    except CleanupRetryableError:
        logger.warning("asset_worker_source_cleanup_retry", upload_id=str(upload_id))
        return
    logger.info("asset_worker_completed", upload_id=str(upload_id))


async def _begin_job(
    auth: InternalRequestAuthenticator,
    settings: Settings,
    worker_id: UUID,
    seller_id: UUID,
    upload_id: UUID,
) -> dict[str, Any]:
    async with auth.trusted_domain_session(_worker_envelope(settings, worker_id)) as session:
        value = await session.scalar(
            text(
                "select app_private.begin_asset_verification("
                ":actor,:seller,:upload,:worker,:lease_seconds)"
            ),
            {
                "actor": worker_id,
                "seller": seller_id,
                "upload": upload_id,
                "worker": _WORKER_LABEL,
                "lease_seconds": 300,
            },
        )
    return _json_object(value)


async def _finalize_ready(
    auth: InternalRequestAuthenticator,
    settings: Settings,
    worker_id: UUID,
    seller_id: UUID,
    upload_id: UUID,
    result: VerifiedAssetResult,
) -> None:
    values = {
        "source_size": result.source_byte_size,
        "source_sha": result.source_sha256_hex,
        "ready_size": result.ready_byte_size,
        "ready_sha": result.ready_sha256_hex,
        "ready_media": result.ready.content_type,
        "magic": result.ready.content_type,
        "scanner_result": "clean",
        "ready_version_id": result.ready.version_id,
        "ready_etag": result.ready.etag,
        "verdict": "ready",
    }
    await _finalize(auth, settings, worker_id, seller_id, upload_id, values)


async def _register_ready_candidate(
    auth: InternalRequestAuthenticator,
    settings: Settings,
    worker_id: UUID,
    seller_id: UUID,
    upload_id: UUID,
    result: VerifiedAssetResult,
) -> None:
    async with auth.trusted_domain_session(_worker_envelope(settings, worker_id)) as session:
        await session.scalar(
            text(
                "select app_private.register_asset_ready_candidate("
                ":actor,:seller,:upload,:worker,:ready_version_id,:ready_etag,"
                ":ready_size,:ready_sha,:ready_media,:magic,:scanner_result)"
            ),
            {
                "actor": worker_id,
                "seller": seller_id,
                "upload": upload_id,
                "worker": _WORKER_LABEL,
                "ready_version_id": result.ready.version_id,
                "ready_etag": result.ready.etag,
                "ready_size": result.ready_byte_size,
                "ready_sha": result.ready_sha256_hex,
                "ready_media": result.ready.content_type,
                "magic": result.ready.content_type,
                "scanner_result": "clean",
            },
        )


async def _reject_job(
    auth: InternalRequestAuthenticator,
    settings: Settings,
    worker_id: UUID,
    seller_id: UUID,
    upload_id: UUID,
    job: VerifyPdfJob,
    _error: Exception,
) -> None:
    values = {
        "source_size": job.expected_size,
        "source_sha": job.expected_sha256_hex,
        "ready_size": job.expected_size,
        "ready_sha": job.expected_sha256_hex,
        "ready_media": job.declared_content_type,
        "magic": job.declared_content_type,
        "scanner_result": "rejected",
        "ready_version_id": "",
        "ready_etag": "",
        "verdict": "rejected",
    }
    try:
        await _finalize(auth, settings, worker_id, seller_id, upload_id, values)
    except DBAPIError:
        logger.warning("asset_worker_rejection_finalize_retry", upload_id=str(upload_id))


async def _finalize(
    auth: InternalRequestAuthenticator,
    settings: Settings,
    worker_id: UUID,
    seller_id: UUID,
    upload_id: UUID,
    values: dict[str, Any],
) -> None:
    serialized = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    idempotency_key = uuid5(
        NAMESPACE_URL,
        f"appsell.asset.finalize\0{upload_id}\0{values['verdict']}",
    )
    async with auth.trusted_domain_session(_worker_envelope(settings, worker_id)) as session:
        await session.scalar(
            text(
                "select app_private.finalize_asset_verification("
                ":actor,:seller,:upload,:worker,:source_size,:source_sha,"
                ":ready_size,:ready_sha,:ready_media,:magic,:scanner_result,"
                ":ready_version_id,:ready_etag,:verdict,:idempotency_key,"
                ":body_sha256,:request_id)"
            ),
            {
                "actor": worker_id,
                "seller": seller_id,
                "upload": upload_id,
                "worker": _WORKER_LABEL,
                **values,
                "idempotency_key": idempotency_key,
                "body_sha256": hashlib.sha256(serialized).hexdigest(),
                "request_id": uuid4(),
            },
        )


def _worker_envelope(settings: Settings, worker_id: UUID) -> ServiceEnvelope:
    now = int(time.time())
    return ServiceEnvelope(
        version=1,
        kid="api-internal",
        issuer=settings.internal_issuer,
        audience=settings.internal_audience,
        session_ref="asset-worker",
        session_epoch=1,
        identity=ServiceIdentity(
            issuer=settings.identity_issuer,
            sub=_WORKER_SUBJECT,
            user_id=worker_id,
        ),
        capability="catalog.asset.verify",
        seller_organization_id=None,
        method="POST",
        path="/v1/internal/asset-worker",
        query_sha256=hashlib.sha256(b"").hexdigest(),
        body_sha256=hashlib.sha256(b"").hexdigest(),
        request_id=uuid4(),
        iat=now,
        exp=now + 60,
    )


def _worker_id(settings: Settings) -> UUID:
    return uuid5(NAMESPACE_URL, f"{settings.identity_issuer}\0{_WORKER_SUBJECT}")


def _json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("invalid asset worker response")
    return value


async def _run_blocking(function: Any, argument: Any) -> Any:
    from anyio import to_thread

    return await to_thread.run_sync(function, argument)
