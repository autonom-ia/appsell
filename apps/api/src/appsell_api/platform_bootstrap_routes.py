from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .routes import _authenticate, _domain_error, _model_from_body, _require_no_seller

router = APIRouter(prefix="/v1/platform")

_ASSURANCE_LEVELS = frozenset({"mfa", "passkey"})
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
# 2^63 - 1: guard the epoch-second attestation window against bigint overflow.
_MAX_EPOCH_SECONDS = 9_223_372_036_854_775_807


class BootstrapSuperadminBody(BaseModel):
    """Attested claims minted by the trusted BFF during superadmin bootstrap.

    Field-level checks fail fast on malformed input; the domain function
    ``app_private.consume_superadmin_bootstrap`` remains the sole authority on
    the trust policy, attestation signature and single-consumption invariant.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    email: str
    email_verified: bool
    assurance: str
    issuer: str
    audience: str
    client_id: str
    attestation_issued_at: Annotated[int, Field(ge=1, le=_MAX_EPOCH_SECONDS)]
    attestation_expires_at: Annotated[int, Field(ge=1, le=_MAX_EPOCH_SECONDS)]
    attestation_signature: str
    source_ip_hash: str | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if (
            value != value.strip().lower()
            or not 3 <= len(value.encode("utf-8")) <= 320
            or value.find("@") <= 0
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        ):
            raise ValueError("invalid normalized email")
        return value

    @field_validator("assurance")
    @classmethod
    def validate_assurance(cls, value: str) -> str:
        if value not in _ASSURANCE_LEVELS:
            raise ValueError("invalid assurance level")
        return value

    @field_validator("issuer", "audience", "client_id")
    @classmethod
    def validate_trust_field(cls, value: str) -> str:
        if (
            value != value.strip()
            or not 3 <= len(value.encode("utf-8")) <= 255
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        ):
            raise ValueError("invalid trust policy field")
        return value

    @field_validator("attestation_signature")
    @classmethod
    def validate_signature(cls, value: str) -> str:
        if _SHA256_HEX.fullmatch(value) is None:
            raise ValueError("invalid attestation signature")
        return value

    @field_validator("source_ip_hash")
    @classmethod
    def validate_source_ip_hash(cls, value: str | None) -> str | None:
        if value is not None and _SHA256_HEX.fullmatch(value) is None:
            raise ValueError("invalid source ip hash")
        return value


@router.post("/bootstrap-superadmin", status_code=status.HTTP_201_CREATED)
async def bootstrap_superadmin(request: Request) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "platform.bootstrap")
    await _require_no_seller(internal)
    body = await _model_from_body(BootstrapSuperadminBody, internal)
    envelope = internal.envelope
    try:
        async with auth.domain_session(envelope) as session:
            identity_id = await session.scalar(
                text(
                    "select app_private.consume_superadmin_bootstrap("
                    ":identity_user_id,:identity_sub,:email_normalized,:email_verified,"
                    ":assurance,:issuer,:audience,:client_id,:request_id,:source_ip_hash,"
                    ":attestation_issued_at,:attestation_expires_at,:attestation_signature)"
                ),
                {
                    "identity_user_id": envelope.identity.user_id,
                    "identity_sub": envelope.identity.sub,
                    "email_normalized": body.email,
                    "email_verified": body.email_verified,
                    "assurance": body.assurance,
                    "issuer": body.issuer,
                    "audience": body.audience,
                    "client_id": body.client_id,
                    "request_id": envelope.request_id,
                    "source_ip_hash": body.source_ip_hash,
                    "attestation_issued_at": body.attestation_issued_at,
                    "attestation_expires_at": body.attestation_expires_at,
                    "attestation_signature": body.attestation_signature,
                },
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    return {"id": str(identity_id)}
