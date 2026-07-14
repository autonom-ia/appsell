from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Settings
from .service_auth import (
    ServiceAuthConfigurationError,
    ServiceAuthError,
    ServiceAuthVerifier,
    ServiceEnvelope,
)

_MAX_BODY_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class _GuardedRequest:
    authenticator_id: int
    task_id: int
    envelope: ServiceEnvelope


_guarded_requests: ContextVar[tuple[_GuardedRequest, ...]] = ContextVar(
    "appsell_guarded_requests", default=()
)
_ALLOWED_CONTINUATIONS = frozenset(
    {
        ("catalog.asset.create", "catalog.asset.upload.sign"),
        ("catalog.asset.upload.confirm", "catalog.asset.upload.sign"),
        ("catalog.asset.upload.confirm", "catalog.asset.upload.confirm"),
    }
)


class ReplayRejector(Protocol):
    async def consume_rejected_request(self, envelope: ServiceEnvelope) -> None: ...


@dataclass(frozen=True, slots=True)
class AuthenticatedInternalRequest:
    envelope: ServiceEnvelope
    body: bytes
    authenticator: ReplayRejector = field(repr=False, compare=False)

    async def consume_rejected_request(self) -> None:
        await self.authenticator.consume_rejected_request(self.envelope)


class InternalRequestAuthenticator:
    def __init__(
        self,
        *,
        verifier: ServiceAuthVerifier,
        service_credential: bytes,
        context_hmac_key: bytes,
        session_factory: async_sessionmaker[AsyncSession],
        internal_issuer: str,
        internal_audience: str,
        identity_issuer: str,
    ) -> None:
        self._verifier = verifier
        self._service_credential = service_credential
        self._context_hmac_key = context_hmac_key
        self._session_factory = session_factory
        self._internal_issuer = internal_issuer
        self._internal_audience = internal_audience
        self._identity_issuer = identity_issuer

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> InternalRequestAuthenticator:
        keyring = _parse_keyring(settings.internal_hmac_keyring_b64_json)
        context_key = _decode_key(settings.context_hmac_key_b64, "context HMAC key")
        credential = _decode_key(settings.service_credential_b64, "service credential")
        signing_keys = [_decode_key(value, "internal HMAC key") for value in keyring.values()]
        if any(hmac.compare_digest(credential, key) for key in [context_key, *signing_keys]):
            raise ServiceAuthConfigurationError("service credentials and signing keys must differ")
        return cls(
            verifier=ServiceAuthVerifier(
                expected_issuer=settings.internal_issuer,
                expected_audience=settings.internal_audience,
                expected_identity_issuer=settings.identity_issuer,
                keyring=keyring,
                context_hmac_key=settings.context_hmac_key_b64,
            ),
            service_credential=credential,
            context_hmac_key=context_key,
            session_factory=session_factory,
            internal_issuer=settings.internal_issuer,
            internal_audience=settings.internal_audience,
            identity_issuer=settings.identity_issuer,
        )

    async def authenticate(
        self,
        request: Request,
        expected_capability: str,
    ) -> AuthenticatedInternalRequest:
        credential = request.headers.get("x-appsell-service-credential", "")
        provided_credential = _decode_presented_credential(credential)
        if provided_credential is None or not hmac.compare_digest(
            provided_credential, self._service_credential
        ):
            raise _unauthorized()

        body = await request.body()
        if len(body) > _MAX_BODY_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        try:
            envelope = self._verifier.verify_request(
                request.headers.get("x-appsell-internal-envelope", ""),
                method=request.method,
                path=request.url.path,
                query=request.scope.get("query_string", b""),
                body=body,
                expected_capability=expected_capability,
            )
        except ServiceAuthError as error:
            raise _unauthorized() from error

        return AuthenticatedInternalRequest(envelope=envelope, body=body, authenticator=self)

    def require_service_credential(self, request: Request) -> None:
        credential = request.headers.get("x-appsell-service-credential", "")
        provided_credential = _decode_presented_credential(credential)
        if provided_credential is None or not hmac.compare_digest(
            provided_credential, self._service_credential
        ):
            raise _unauthorized()

    async def consume_rejected_request(self, envelope: ServiceEnvelope) -> None:
        try:
            async with self._session_factory() as session, session.begin():
                await session.execute(
                    text("select app_private.consume_bff_request(:request_id, :expires_at)"),
                    {"request_id": envelope.request_id, "expires_at": envelope.exp},
                )
        except IntegrityError as error:
            raise _unauthorized() from error
        except DBAPIError as error:
            if _sqlstate(error) == "28000":
                raise _unauthorized() from error
            raise _request_guard_unavailable() from error

    @asynccontextmanager
    async def domain_session(self, envelope: ServiceEnvelope) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            transaction = await session.begin()
            try:
                await session.execute(
                    text("select app_private.consume_bff_request(:request_id, :expires_at)"),
                    {"request_id": envelope.request_id, "expires_at": envelope.exp},
                )
            except IntegrityError as error:
                await transaction.rollback()
                raise _unauthorized() from error
            except DBAPIError as error:
                await transaction.rollback()
                if _sqlstate(error) == "28000":
                    raise _unauthorized() from error
                raise _request_guard_unavailable() from error

            domain = await session.begin_nested()
            try:
                await self._apply_context(session, envelope)
                yield session
                # Force every deferred domain invariant to fail inside the
                # savepoint. A violation can then roll back business work while
                # the outer transaction still durably consumes the replay ID.
                await session.execute(text("set constraints all immediate"))
                await domain.commit()
            except Exception:
                if domain.is_active:
                    await domain.rollback()
                try:
                    await transaction.commit()
                except DBAPIError as error:
                    raise _request_guard_unavailable() from error
                raise
            else:
                await transaction.commit()
                self._mark_guarded(envelope)
            finally:
                if transaction.is_active:
                    await transaction.rollback()

    @asynccontextmanager
    async def trusted_domain_session(
        self, envelope: ServiceEnvelope
    ) -> AsyncIterator[AsyncSession]:
        if not _is_asset_worker_envelope(
            envelope,
            internal_issuer=self._internal_issuer,
            internal_audience=self._internal_audience,
            identity_issuer=self._identity_issuer,
        ):
            raise _unauthorized()
        async with self._session_factory() as session, session.begin():
            await self._apply_context(session, envelope)
            yield session

    @asynccontextmanager
    async def continuation_domain_session(
        self, envelope: ServiceEnvelope
    ) -> AsyncIterator[AsyncSession]:
        task = asyncio.current_task()
        if task is None or envelope.exp < int(time.time()):
            raise _unauthorized()
        for guarded in _guarded_requests.get():
            if (
                guarded.authenticator_id == id(self)
                and guarded.task_id == id(task)
                and replace(envelope, capability=guarded.envelope.capability) == guarded.envelope
                and (guarded.envelope.capability, envelope.capability) in _ALLOWED_CONTINUATIONS
            ):
                break
        else:
            raise _unauthorized()
        async with self._session_factory() as session, session.begin():
            await self._apply_context(session, envelope)
            yield session

    def _mark_guarded(self, envelope: ServiceEnvelope) -> None:
        task = asyncio.current_task()
        if task is None:
            return
        now = int(time.time())
        current = tuple(
            guarded
            for guarded in _guarded_requests.get()
            if guarded.envelope.exp >= now
            and not (
                guarded.authenticator_id == id(self)
                and guarded.task_id == id(task)
                and guarded.envelope.request_id == envelope.request_id
            )
        )
        _guarded_requests.set((*current, _GuardedRequest(id(self), id(task), envelope)))

    async def _apply_context(self, session: AsyncSession, envelope: ServiceEnvelope) -> None:
        now = int(time.time())
        issued_at = max(envelope.iat, now - 5)
        expires_at = min(envelope.exp, now + 60)
        seller = str(envelope.seller_organization_id) if envelope.seller_organization_id else ""
        payload = (
            f"{envelope.identity.user_id}:{seller}:{envelope.capability}:{issued_at}:{expires_at}"
        )
        signature = hmac.new(
            self._context_hmac_key, payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        await session.execute(
            text(
                "select app_private.apply_request_context("
                ":identity_user_id, :seller_organization_id, :capability, "
                ":issued_at, :expires_at, :signature)"
            ),
            {
                "identity_user_id": envelope.identity.user_id,
                "seller_organization_id": envelope.seller_organization_id,
                "capability": envelope.capability,
                "issued_at": issued_at,
                "expires_at": expires_at,
                "signature": signature,
            },
        )


def _parse_keyring(serialized: str) -> Mapping[str, str]:
    try:
        value = json.loads(serialized)
    except json.JSONDecodeError as error:
        raise ServiceAuthConfigurationError("internal HMAC keyring must be valid JSON") from error
    if not isinstance(value, list) or not 1 <= len(value) <= 4:
        raise ServiceAuthConfigurationError("internal HMAC keyring must contain 1 to 4 keys")
    result: dict[str, str] = {}
    active = 0
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"id", "key", "active"}:
            raise ServiceAuthConfigurationError("internal HMAC key entry is invalid")
        key_id, encoded, is_active = entry["id"], entry["key"], entry["active"]
        if (
            not isinstance(key_id, str)
            or not isinstance(encoded, str)
            or type(is_active) is not bool
        ):
            raise ServiceAuthConfigurationError("internal HMAC key entry is invalid")
        if key_id in result:
            raise ServiceAuthConfigurationError("internal HMAC key IDs must be unique")
        result[key_id] = encoded
        active += int(is_active)
    if active != 1:
        raise ServiceAuthConfigurationError("internal HMAC keyring needs exactly one active key")
    return result


def _decode_key(value: str, label: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ServiceAuthConfigurationError(f"{label} must be canonical base64") from error
    if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != value:
        raise ServiceAuthConfigurationError(f"{label} must contain 32 bytes")
    return decoded


def _decode_presented_credential(value: str) -> bytes | None:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != value:
        return None
    return decoded


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid_internal_request",
    )


def _request_guard_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="request_guard_unavailable",
    )


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)


def _is_asset_worker_envelope(
    envelope: ServiceEnvelope,
    *,
    internal_issuer: str,
    internal_audience: str,
    identity_issuer: str,
) -> bool:
    now = int(time.time())
    return (
        envelope.version == 1
        and envelope.kid == "api-internal"
        and envelope.issuer == internal_issuer
        and envelope.audience == internal_audience
        and envelope.session_ref == "asset-worker"
        and envelope.session_epoch == 1
        and envelope.identity.issuer == identity_issuer
        and envelope.identity.sub == "asset-worker-v1"
        and envelope.identity.user_id == uuid5(NAMESPACE_URL, f"{identity_issuer}\0asset-worker-v1")
        and envelope.capability == "catalog.asset.verify"
        and envelope.seller_organization_id is None
        and envelope.method == "POST"
        and envelope.path == "/v1/internal/asset-worker"
        and envelope.query_sha256 == hashlib.sha256(b"").hexdigest()
        and envelope.body_sha256 == hashlib.sha256(b"").hexdigest()
        and 1 <= envelope.iat <= now + 5
        and envelope.iat < envelope.exp <= envelope.iat + 60
        and envelope.exp >= now - 5
    )
