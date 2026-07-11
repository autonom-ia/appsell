from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

_TOKEN_PREFIX = "v1"  # noqa: S105 - protocol version, not a credential
_SIGNING_PREFIX = b"appsell.internal.v1."
_MAX_PAYLOAD_BYTES = 4 * 1024
_MAX_TOKEN_BYTES = 8 * 1024
_MAX_TTL_SECONDS = 60
_MAX_CLOCK_SKEW_SECONDS = 5
_MAX_SAFE_INTEGER = (1 << 53) - 1
_KEY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
_SERVICE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,119}$")
_CAPABILITY_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_PATH_PATTERN = re.compile(r"^/[a-z0-9_-]+(?:/[a-z0-9_-]+)*$")
_EXPECTED_FIELDS = frozenset(
    {
        "version",
        "kid",
        "issuer",
        "audience",
        "session_ref",
        "session_epoch",
        "identity",
        "capability",
        "seller_organization_id",
        "method",
        "path",
        "query_sha256",
        "body_sha256",
        "request_id",
        "iat",
        "exp",
    }
)
_EXPECTED_IDENTITY_FIELDS = frozenset({"issuer", "sub", "user_id"})


class ServiceAuthError(ValueError):
    """The internal service token is malformed, untrusted, or expired."""


class ServiceAuthConfigurationError(ValueError):
    """The verifier keyring or trust policy is unsafe."""


@dataclass(frozen=True, slots=True)
class ServiceIdentity:
    issuer: str
    sub: str
    user_id: UUID


@dataclass(frozen=True, slots=True)
class ServiceEnvelope:
    version: int
    kid: str
    issuer: str
    audience: str
    session_ref: str
    session_epoch: int
    identity: ServiceIdentity
    capability: str
    seller_organization_id: UUID | None
    method: str
    path: str
    query_sha256: str
    body_sha256: str
    request_id: UUID
    iat: int
    exp: int


class ServiceAuthVerifier:
    def __init__(
        self,
        *,
        expected_issuer: str,
        expected_audience: str,
        expected_identity_issuer: str,
        keyring: Mapping[str, str],
        context_hmac_key: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._expected_issuer = _trusted_name(expected_issuer, "issuer")
        self._expected_audience = _trusted_name(expected_audience, "audience")
        self._expected_identity_issuer = _trusted_identity_issuer(expected_identity_issuer)
        self._clock = clock
        if not keyring:
            raise ServiceAuthConfigurationError("service auth keyring must not be empty")

        decoded_keyring: dict[str, bytes] = {}
        for kid, encoded_key in keyring.items():
            if not isinstance(kid, str) or _KEY_ID_PATTERN.fullmatch(kid) is None:
                raise ServiceAuthConfigurationError("service auth key ID is invalid")
            if kid in decoded_keyring:
                raise ServiceAuthConfigurationError("service auth key ID is duplicated")
            decoded_keyring[kid] = _decode_key(encoded_key, "service auth key")

        context_key = _decode_key(context_hmac_key, "context HMAC key")
        if any(hmac.compare_digest(context_key, key) for key in decoded_keyring.values()):
            raise ServiceAuthConfigurationError(
                "service auth and database context HMAC keys must be distinct"
            )
        self._keyring = decoded_keyring

    def verify_request(
        self,
        token: str,
        *,
        method: str,
        path: str,
        query: bytes,
        body: bytes,
        expected_capability: str,
    ) -> ServiceEnvelope:
        try:
            envelope = self._verify(token)
            if (
                envelope.method != method
                or envelope.path != path
                or envelope.query_sha256 != hashlib.sha256(query).hexdigest()
                or envelope.body_sha256 != hashlib.sha256(body).hexdigest()
                or envelope.capability != expected_capability
            ):
                raise ServiceAuthError("invalid internal service token")
            return envelope
        except ServiceAuthError:
            raise
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
            raise ServiceAuthError("invalid internal service token") from error

    def _verify(self, token: str) -> ServiceEnvelope:
        if not isinstance(token, str) or not token.isascii():
            raise ServiceAuthError("invalid internal service token")
        if not 1 <= len(token) <= _MAX_TOKEN_BYTES:
            raise ServiceAuthError("invalid internal service token")
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != _TOKEN_PREFIX:
            raise ServiceAuthError("invalid internal service token")

        payload_segment, signature_segment = parts[1], parts[2]
        payload_bytes = _decode_canonical_base64url(payload_segment)
        signature = _decode_canonical_base64url(signature_segment)
        if not payload_bytes or len(payload_bytes) > _MAX_PAYLOAD_BYTES or len(signature) != 32:
            raise ServiceAuthError("invalid internal service token")

        payload = _decode_json_object(payload_bytes)
        if set(payload) != _EXPECTED_FIELDS:
            raise ServiceAuthError("invalid internal service token")
        kid = _required_string(payload, "kid")
        if _KEY_ID_PATTERN.fullmatch(kid) is None:
            raise ServiceAuthError("invalid internal service token")
        signing_key = self._keyring.get(kid)
        if signing_key is None:
            raise ServiceAuthError("invalid internal service token")

        signing_input = _SIGNING_PREFIX + payload_segment.encode("ascii")
        expected_signature = hmac.digest(signing_key, signing_input, "sha256")
        if not hmac.compare_digest(expected_signature, signature):
            raise ServiceAuthError("invalid internal service token")

        return self._validate_payload(payload)

    def _validate_payload(self, payload: dict[str, Any]) -> ServiceEnvelope:
        version = _required_integer(payload, "version")
        if version != 1:
            raise ServiceAuthError("invalid internal service token")

        issuer = _required_string(payload, "issuer")
        audience = _required_string(payload, "audience")
        if issuer != self._expected_issuer or audience != self._expected_audience:
            raise ServiceAuthError("invalid internal service token")

        identity_payload = payload["identity"]
        if (
            not isinstance(identity_payload, dict)
            or set(identity_payload) != _EXPECTED_IDENTITY_FIELDS
        ):
            raise ServiceAuthError("invalid internal service token")
        identity_issuer = _required_string(identity_payload, "issuer")
        subject = _required_string(identity_payload, "sub")
        if identity_issuer != self._expected_identity_issuer or not _safe_subject(subject):
            raise ServiceAuthError("invalid internal service token")
        identity = ServiceIdentity(
            issuer=identity_issuer,
            sub=subject,
            user_id=_canonical_uuid(_required_string(identity_payload, "user_id")),
        )
        if identity.user_id != uuid5(NAMESPACE_URL, f"{identity.issuer}\0{identity.sub}"):
            raise ServiceAuthError("invalid internal service token")

        session_ref = _required_string(payload, "session_ref")
        query_sha256 = _required_string(payload, "query_sha256")
        body_sha256 = _required_string(payload, "body_sha256")
        if _SHA256_PATTERN.fullmatch(session_ref) is None:
            raise ServiceAuthError("invalid internal service token")
        if _SHA256_PATTERN.fullmatch(query_sha256) is None:
            raise ServiceAuthError("invalid internal service token")
        if _SHA256_PATTERN.fullmatch(body_sha256) is None:
            raise ServiceAuthError("invalid internal service token")

        session_epoch = _required_integer(payload, "session_epoch")
        if not 1 <= session_epoch <= _MAX_SAFE_INTEGER:
            raise ServiceAuthError("invalid internal service token")

        capability = _required_string(payload, "capability")
        if _CAPABILITY_PATTERN.fullmatch(capability) is None:
            raise ServiceAuthError("invalid internal service token")

        seller_value = payload["seller_organization_id"]
        if seller_value is None:
            seller_id = None
        elif isinstance(seller_value, str):
            seller_id = _canonical_uuid(seller_value)
        else:
            raise ServiceAuthError("invalid internal service token")

        method = _required_string(payload, "method")
        path = _required_string(payload, "path")
        if method not in _ALLOWED_METHODS or not _canonical_path(path):
            raise ServiceAuthError("invalid internal service token")

        request_id = _canonical_uuid(_required_string(payload, "request_id"))
        issued_at = _required_integer(payload, "iat")
        expires_at = _required_integer(payload, "exp")
        now = int(self._clock())
        if (
            not 1 <= issued_at <= _MAX_SAFE_INTEGER
            or not 1 <= expires_at <= _MAX_SAFE_INTEGER
            or expires_at <= issued_at
            or expires_at - issued_at > _MAX_TTL_SECONDS
            or issued_at > now + _MAX_CLOCK_SKEW_SECONDS
            or expires_at < now - _MAX_CLOCK_SKEW_SECONDS
        ):
            raise ServiceAuthError("invalid internal service token")

        return ServiceEnvelope(
            version=version,
            kid=_required_string(payload, "kid"),
            issuer=issuer,
            audience=audience,
            session_ref=session_ref,
            session_epoch=session_epoch,
            identity=identity,
            capability=capability,
            seller_organization_id=seller_id,
            method=method,
            path=path,
            query_sha256=query_sha256,
            body_sha256=body_sha256,
            request_id=request_id,
            iat=issued_at,
            exp=expires_at,
        )


def _trusted_name(value: str, label: str) -> str:
    if not isinstance(value, str) or _SERVICE_NAME_PATTERN.fullmatch(value) is None:
        raise ServiceAuthConfigurationError(f"service auth {label} is invalid")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ServiceAuthConfigurationError(f"service auth {label} is invalid")
    return value


def _trusted_identity_issuer(value: str) -> str:
    if (
        not isinstance(value, str)
        or value.startswith(" ")
        or value.endswith(" ")
        or any(unicodedata.category(character) in {"Cc", "Cs"} for character in value)
        or not 3 <= len(value.encode("utf-8")) <= 255
    ):
        raise ServiceAuthConfigurationError("service auth identity issuer is invalid")
    return value


def _decode_key(value: str, label: str) -> bytes:
    if not isinstance(value, str) or not value.isascii():
        raise ServiceAuthConfigurationError(f"{label} must be canonical base64")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ServiceAuthConfigurationError(f"{label} must be canonical base64") from error
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ServiceAuthConfigurationError(f"{label} must be canonical base64")
    if len(decoded) != 32:
        raise ServiceAuthConfigurationError(f"{label} must contain exactly 32 bytes")
    return decoded


def _decode_canonical_base64url(value: str) -> bytes:
    if not isinstance(value, str) or not value or not value.isascii() or "=" in value:
        raise ServiceAuthError("invalid internal service token")
    if re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise ServiceAuthError("invalid internal service token")
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as error:
        raise ServiceAuthError("invalid internal service token") from error
    if _encode_base64url(decoded) != value:
        raise ServiceAuthError("invalid internal service token")
    return decoded


def _encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_json_object(value: bytes) -> dict[str, Any]:
    try:
        serialized = value.decode("utf-8", errors="strict")
        parsed = json.loads(
            serialized,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: _raise_invalid_json(),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ServiceAuthError) as error:
        raise ServiceAuthError("invalid internal service token") from error
    if not isinstance(parsed, dict):
        raise ServiceAuthError("invalid internal service token")
    return parsed


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ServiceAuthError("invalid internal service token")
        result[key] = value
    return result


def _raise_invalid_json() -> None:
    raise ServiceAuthError("invalid internal service token")


def _required_string(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ServiceAuthError("invalid internal service token")
    return value


def _required_integer(payload: Mapping[str, Any], field: str) -> int:
    value = payload.get(field)
    if type(value) is not int:
        raise ServiceAuthError("invalid internal service token")
    return value


def _canonical_uuid(value: str) -> UUID:
    if _UUID_PATTERN.fullmatch(value) is None:
        raise ServiceAuthError("invalid internal service token")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise ServiceAuthError("invalid internal service token") from error
    if str(parsed) != value:
        raise ServiceAuthError("invalid internal service token")
    return parsed


def _safe_subject(value: str) -> bool:
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
        return False
    encoded = value.encode("utf-8")
    return not value.startswith(" ") and not value.endswith(" ") and 3 <= len(encoded) <= 255


def _canonical_path(value: str) -> bool:
    return 1 <= len(value) <= 160 and _PATH_PATTERN.fullmatch(value) is not None
