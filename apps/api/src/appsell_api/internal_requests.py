from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

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
class AuthenticatedInternalRequest:
    envelope: ServiceEnvelope
    body: bytes


class InternalRequestAuthenticator:
    def __init__(
        self,
        *,
        verifier: ServiceAuthVerifier,
        service_credential: bytes,
        context_hmac_key: bytes,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._verifier = verifier
        self._service_credential = service_credential
        self._context_hmac_key = context_hmac_key
        self._session_factory = session_factory

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

        try:
            async with self._session_factory() as replay_session, replay_session.begin():
                await replay_session.execute(
                    text("select app_private.consume_bff_request(:request_id, :expires_at)"),
                    {"request_id": envelope.request_id, "expires_at": envelope.exp},
                )
        except IntegrityError as error:
            raise _unauthorized() from error
        except DBAPIError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="request_guard_unavailable",
            ) from error
        return AuthenticatedInternalRequest(envelope=envelope, body=body)

    def require_service_credential(self, request: Request) -> None:
        credential = request.headers.get("x-appsell-service-credential", "")
        provided_credential = _decode_presented_credential(credential)
        if provided_credential is None or not hmac.compare_digest(
            provided_credential, self._service_credential
        ):
            raise _unauthorized()

    @asynccontextmanager
    async def domain_session(self, envelope: ServiceEnvelope) -> AsyncIterator[AsyncSession]:
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
        async with self._session_factory() as session, session.begin():
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
            yield session


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
