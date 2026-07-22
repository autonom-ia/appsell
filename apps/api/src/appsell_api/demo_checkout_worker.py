from __future__ import annotations

import asyncio
import hashlib
import os
import re
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

_WORKER_SUBJECT = "demo-checkout-worker-v1"
_WORKER_LABEL = f"api-demo-checkout-{os.getpid()}"
_IDLE_INTERVAL_SECONDS = 0.25
_MAX_BACKOFF_SECONDS = 30.0
_BACKOFF_FACTOR = 2.0
_MAX_LOGGED_ERROR_CHARS = 300
# Sequências opacas longas (tokens, chaves, credenciais base64) nunca vão para o log.
_OPAQUE_VALUE = re.compile(r"[A-Za-z0-9_\-]{32,}")
logger = structlog.get_logger(__name__)


def _sanitized_reason(error: BaseException) -> str:
    return _OPAQUE_VALUE.sub("<REDACTED>", str(error))[:_MAX_LOGGED_ERROR_CHARS]


async def run_demo_checkout_worker_loop(app: FastAPI, stop: asyncio.Event) -> None:
    settings: Settings = app.state.settings
    if not settings.demo_checkout_worker_active:
        return
    auth: InternalRequestAuthenticator | None = app.state.internal_auth
    if auth is None:
        return
    worker_id = _worker_id(settings)
    consecutive_failures = 0
    while not stop.is_set():
        delay = _IDLE_INTERVAL_SECONDS
        try:
            jobs = await _claim(auth, settings, worker_id)
            for job in jobs:
                await _apply(auth, settings, worker_id, job)
            consecutive_failures = 0
            if jobs:
                continue
        except (DBAPIError, RuntimeError, ValueError, KeyError) as error:
            consecutive_failures += 1
            delay = _retry_delay(consecutive_failures)
            # Sem o motivo, uma falha permanente (função ausente, RLS negando)
            # vira ruído indistinguível de uma indisponibilidade passageira.
            logger.warning(
                "demo_checkout_worker_retry",
                error_type=type(error).__name__,
                error=_sanitized_reason(error),
                backoff_seconds=delay,
                consecutive_failures=consecutive_failures,
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass


def _retry_delay(consecutive_failures: int) -> float:
    growth = _BACKOFF_FACTOR ** (consecutive_failures - 1)
    return min(_IDLE_INTERVAL_SECONDS * growth, _MAX_BACKOFF_SECONDS)


async def _claim(
    auth: InternalRequestAuthenticator, settings: Settings, worker_id: UUID
) -> list[dict[str, Any]]:
    async with auth.trusted_domain_session(_worker_envelope(settings, worker_id)) as session:
        value = await session.scalar(
            text("select app_private.claim_demo_checkout_jobs(:worker,:label,4,30)"),
            {"worker": worker_id, "label": _WORKER_LABEL},
        )
    if not isinstance(value, list) or any(not isinstance(job, dict) for job in value):
        raise RuntimeError("invalid demo checkout claim response")
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
                    "select app_private.apply_demo_checkout_entitlement("
                    ":worker,:label,:seller,:job,:fence,:request_id)"
                ),
                {
                    "worker": worker_id,
                    "label": _WORKER_LABEL,
                    "seller": seller_id,
                    "job": job_id,
                    "fence": fence,
                    "request_id": uuid4(),
                },
            )
        if not isinstance(result, dict):
            raise RuntimeError("invalid demo checkout apply response")
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
                    "select app_private.fail_demo_checkout_job("
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
        logger.warning("demo_checkout_failure_record_unavailable", job_id=str(job_id))


def _worker_envelope(
    settings: Settings, worker_id: UUID, seller_id: UUID | None = None
) -> ServiceEnvelope:
    now = int(time.time())
    return ServiceEnvelope(
        version=1,
        kid="api-internal",
        issuer=settings.internal_issuer,
        audience=settings.internal_audience,
        session_ref="demo-checkout-worker",
        session_epoch=1,
        identity=ServiceIdentity(
            issuer=settings.identity_issuer,
            sub=_WORKER_SUBJECT,
            user_id=worker_id,
        ),
        capability="commerce.demo.checkout.worker",
        seller_organization_id=seller_id,
        method="POST",
        path="/v1/internal/demo-checkout-worker",
        query_sha256=hashlib.sha256(b"").hexdigest(),
        body_sha256=hashlib.sha256(b"").hexdigest(),
        request_id=uuid4(),
        iat=now,
        exp=now + 60,
    )


def _worker_id(settings: Settings) -> UUID:
    return uuid5(NAMESPACE_URL, f"{settings.identity_issuer}\0{_WORKER_SUBJECT}")
