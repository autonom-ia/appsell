from __future__ import annotations

import hashlib
import os
import re
import secrets
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .routes import _authenticate, _domain_error, _model_from_body, _require_no_seller

router = APIRouter(prefix="/v1/platform/demo-inspections")
_OPAQUE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class InspectionStartBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reason: str
    ttl_seconds: Annotated[int, Field(ge=30, le=900)]

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _validated_reason(value)


class InspectionRevokeBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reason: str

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _validated_reason(value)


@router.post("", status_code=status.HTTP_201_CREATED)
async def start_demo_inspection(request: Request) -> dict[str, Any]:
    _require_local_ci()
    auth, internal = await _authenticate(request, "platform.demo.inspect.start")
    _require_no_seller(internal)
    body = _model_from_body(InspectionStartBody, internal.body)
    token = secrets.token_urlsafe(32)
    if _OPAQUE_TOKEN.fullmatch(token) is None:  # defensive: token_urlsafe contract
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    envelope = internal.envelope
    try:
        async with auth.domain_session(envelope) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "select * from app_private.start_demo_inspection("
                            ":identity_user_id,:identity_sub,:reason,:ttl_seconds,"
                            ":token_sha256,:request_id)"
                        ),
                        {
                            "identity_user_id": envelope.identity.user_id,
                            "identity_sub": envelope.identity.sub,
                            "reason": body.reason,
                            "ttl_seconds": body.ttl_seconds,
                            "token_sha256": _token_sha256(token),
                            "request_id": envelope.request_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    return {
        "inspection_id": str(row["inspection_id"]),
        "inspection_token": token,
        "expires_at": row["expires_at"],
        "seller": {
            "id": str(row["seller_id"]),
            "slug": row["seller_slug"],
            "display_name": row["seller_display_name"],
        },
    }


@router.get("/{inspection_id}")
async def read_demo_inspection(inspection_id: UUID, request: Request) -> dict[str, Any]:
    _require_local_ci()
    auth, internal = await _authenticate(request, "platform.demo.inspect.read")
    _require_no_seller(internal)
    token_hash = _inspection_token_hash(request)
    envelope = internal.envelope
    try:
        async with auth.domain_session(envelope) as session:
            projection = await session.scalar(
                text(
                    "select app_private.read_demo_inspection("
                    ":identity_user_id,:identity_sub,:inspection_id,"
                    ":token_sha256,:request_id)"
                ),
                {
                    "identity_user_id": envelope.identity.user_id,
                    "identity_sub": envelope.identity.sub,
                    "inspection_id": inspection_id,
                    "token_sha256": token_hash,
                    "request_id": envelope.request_id,
                },
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    if projection is None:
        raise _inspection_not_found()
    return _json_object(projection)


@router.post("/{inspection_id}/revoke")
async def revoke_demo_inspection(inspection_id: UUID, request: Request) -> dict[str, Any]:
    _require_local_ci()
    auth, internal = await _authenticate(request, "platform.demo.inspect.revoke")
    _require_no_seller(internal)
    body = _model_from_body(InspectionRevokeBody, internal.body)
    token_hash = _inspection_token_hash(request)
    envelope = internal.envelope
    try:
        async with auth.domain_session(envelope) as session:
            result = await session.scalar(
                text(
                    "select app_private.revoke_demo_inspection("
                    ":identity_user_id,:identity_sub,:inspection_id,"
                    ":token_sha256,:reason,:request_id)"
                ),
                {
                    "identity_user_id": envelope.identity.user_id,
                    "identity_sub": envelope.identity.sub,
                    "inspection_id": inspection_id,
                    "token_sha256": token_hash,
                    "reason": body.reason,
                    "request_id": envelope.request_id,
                },
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    if result is None:
        raise _inspection_not_found()
    return _json_object(result)


def _validated_reason(value: str) -> str:
    if (
        value != value.strip()
        or not 8 <= len(value.encode("utf-8")) <= 500
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError("invalid inspection reason")
    return value


def _require_local_ci() -> None:
    # Keep this kill switch independent from unrelated runtime configuration.
    # Missing, malformed, staging and production values all fail closed.
    if os.environ.get("APPSELL_API_ENVIRONMENT", "").strip().lower() not in {"local", "ci"}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="not_found",
        )


def _inspection_token_hash(request: Request) -> str:
    token = request.headers.get("x-appsell-inspection-token", "")
    if _OPAQUE_TOKEN.fullmatch(token) is None:
        raise _inspection_not_found()
    return _token_sha256(token)


def _token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _inspection_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="inspection_not_found",
    )


def _json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="invalid_inspection_projection",
        )
    return cast(dict[str, Any], value)
