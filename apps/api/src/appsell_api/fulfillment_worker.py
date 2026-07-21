"""Production fulfillment worker: consumes fulfillment jobs into entitlements.

Clone of the demo checkout worker protocol (lease/fence via SQL functions from
migration 0020), without the local/ci environment gate: paid access must be
granted in production. The worker self-registers its identity on startup via
``ensure_fulfillment_worker`` (allowlist row; revoking it stops the worker
without key rotation) and then loops claim -> apply with retry recording.
"""

from __future__ import annotations

import asyncio
import hashlib
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
from .service_auth import ServiceEnvelope, ServiceIdentity

_WORKER_SUBJECT = "fulfillment-worker-v1"
_WORKER_EMAIL = "fulfillment-worker@system.appsell.internal"
_WORKER_LABEL = f"api-fulfillment-{os.getpid()}"
_IDLE_POLL_SECONDS = 1.0
logger = structlog.get_logger(__name__)


async def run_fulfillment_worker_loop(app: FastAPI, stop: asyncio.Event) -> None:
    settings: Settings = app.state.settings
    auth: InternalRequestAuthenticator | None = app.state.internal_auth
    if auth is None:
        return
    worker_id = _worker_id(settings)
    registered = False
    while not stop.is_set():
        try:
            if not registered:
                await _ensure_identity(auth, settings, worker_id)
                registered = True
            jobs = await _claim(auth, settings, worker_id)
            for job in jobs:
                await _apply(auth, settings, worker_id, job)
            if jobs:
                continue
        except (DBAPIError, RuntimeError, ValueError, KeyError):
            logger.warning("fulfillment_worker_retry")
        try:
            await asyncio.wait_for(stop.wait(), timeout=_IDLE_POLL_SECONDS)
        except TimeoutError:
            pass


async def _ensure_identity(
    auth: InternalRequestAuthenticator, settings: Settings, worker_id: UUID
) -> None:
    async with auth.trusted_domain_session(_worker_envelope(settings, worker_id)) as session:
        await session.execute(
            text(
                "select app_private.ensure_fulfillment_worker("
                ":worker,:issuer,:subject,:email,:label)"
            ),
            {
                "worker": worker_id,
                "issuer": settings.identity_issuer,
                "subject": _WORKER_SUBJECT,
                "email": _WORKER_EMAIL,
                "label": _WORKER_LABEL,
            },
        )


async def _claim(
    auth: InternalRequestAuthenticator, settings: Settings, worker_id: UUID
) -> list[dict[str, Any]]:
    async with auth.trusted_domain_session(_worker_envelope(settings, worker_id)) as session:
        value = await session.scalar(
            text("select app_private.claim_fulfillment_jobs(:worker,:label,4,30)"),
            {"worker": worker_id, "label": _WORKER_LABEL},
        )
    if not isinstance(value, list) or any(not isinstance(job, dict) for job in value):
        raise RuntimeError("invalid fulfillment claim response")
    return value


async def _apply(
    auth: InternalRequestAuthenticator,
    settings: Settings,
    worker_id: UUID,
    job: dict[str, Any],
) -> None:
    seller_id = UUID(str(job["seller_organization_id"]))
    job_id = UUID(str(job["id"]))
    fence = int(job["lease_fence"])
    try:
        async with auth.trusted_domain_session(
            _worker_envelope(settings, worker_id, seller_id)
        ) as session:
            result = await session.scalar(
                text(
                    "select app_private.apply_checkout_fulfillment("
                    ":worker,:label,:seller,:job,:fence,:request_id,:issuer)"
                ),
                {
                    "worker": worker_id,
                    "label": _WORKER_LABEL,
                    "seller": seller_id,
                    "job": job_id,
                    "fence": fence,
                    "request_id": uuid4(),
                    "issuer": settings.identity_issuer,
                },
            )
        if not isinstance(result, dict):
            raise RuntimeError("invalid fulfillment apply response")
        if result.get("status") in {"dead_letter", "retry"}:
            logger.warning(
                "fulfillment_job_not_applied",
                job_id=str(job_id),
                status=result.get("status"),
                error_code=result.get("error_code"),
            )
    except DBAPIError:
        await _record_failure(auth, settings, worker_id, seller_id, job_id, fence)


async def _record_failure(
    auth: InternalRequestAuthenticator,
    settings: Settings,
    worker_id: UUID,
    seller_id: UUID,
    job_id: UUID,
    fence: int,
) -> None:
    try:
        async with auth.trusted_domain_session(
            _worker_envelope(settings, worker_id, seller_id)
        ) as session:
            await session.scalar(
                text(
                    "select app_private.fail_fulfillment_job("
                    ":worker,:label,:seller,:job,:fence,'apply_retryable',5)"
                ),
                {
                    "worker": worker_id,
                    "label": _WORKER_LABEL,
                    "seller": seller_id,
                    "job": job_id,
                    "fence": fence,
                },
            )
    except DBAPIError:
        logger.warning("fulfillment_failure_record_unavailable", job_id=str(job_id))


def _worker_envelope(
    settings: Settings, worker_id: UUID, seller_id: UUID | None = None
) -> ServiceEnvelope:
    now = int(time.time())
    return ServiceEnvelope(
        version=1,
        kid="api-internal",
        issuer=settings.internal_issuer,
        audience=settings.internal_audience,
        session_ref="fulfillment-worker",
        session_epoch=1,
        identity=ServiceIdentity(
            issuer=settings.identity_issuer,
            sub=_WORKER_SUBJECT,
            user_id=worker_id,
        ),
        capability="commerce.fulfillment.worker",
        seller_organization_id=seller_id,
        method="POST",
        path="/v1/internal/fulfillment-worker",
        query_sha256=hashlib.sha256(b"").hexdigest(),
        body_sha256=hashlib.sha256(b"").hexdigest(),
        request_id=uuid4(),
        iat=now,
        exp=now + 60,
    )


def _worker_id(settings: Settings) -> UUID:
    return uuid5(NAMESPACE_URL, f"{settings.identity_issuer}\0{_WORKER_SUBJECT}")
