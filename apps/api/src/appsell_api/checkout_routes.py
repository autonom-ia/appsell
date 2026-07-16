from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .internal_requests import InternalRequestAuthenticator
from .learning_routes import _canonical_uuid, _model_from_internal_body

router = APIRouter(prefix="/v1/me/demo-checkout")


class DemoCheckoutBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    coupon_code: str

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _canonical_uuid(value, "invalid idempotency key")

    @field_validator("coupon_code")
    @classmethod
    def validate_coupon_code(cls, value: str) -> str:
        if value != value.strip().upper() or not 3 <= len(value.encode("utf-8")) <= 32:
            raise ValueError("invalid coupon code")
        if not value.isascii() or not all(
            character.isalnum() or character in "-_" for character in value
        ):
            raise ValueError("invalid coupon code")
        return value


@router.get("")
async def read_demo_checkout(request: Request) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "commerce.demo.checkout.read")
    _require_local_ci(request)
    try:
        async with auth.domain_session(internal.envelope) as session:
            checkout = await session.scalar(
                text("select app_private.read_demo_checkout(:actor)"),
                {"actor": internal.envelope.identity.user_id},
            )
    except DBAPIError as error:
        raise _checkout_domain_error(error) from error
    if not isinstance(checkout, dict):
        raise HTTPException(status_code=503, detail="demo_checkout_unavailable")
    return {"checkout": checkout}


@router.post("")
async def complete_demo_checkout(request: Request) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "commerce.demo.checkout.complete")
    _require_local_ci(request)
    body = await _model_from_internal_body(request, internal, DemoCheckoutBody)
    try:
        async with auth.domain_session(internal.envelope) as session:
            checkout = await session.scalar(
                text(
                    "select app_private.complete_demo_zero_checkout("
                    ":actor,:coupon_sha256,:idempotency,:body_sha256,:request_id)"
                ),
                {
                    "actor": internal.envelope.identity.user_id,
                    "coupon_sha256": hashlib.sha256(body.coupon_code.encode("utf-8")).hexdigest(),
                    "idempotency": UUID(body.idempotency_key),
                    "body_sha256": internal.envelope.body_sha256,
                    "request_id": internal.envelope.request_id,
                },
            )
    except DBAPIError as error:
        raise _checkout_domain_error(error, completing=True) from error
    if not isinstance(checkout, dict):
        raise HTTPException(status_code=503, detail="demo_checkout_unavailable")
    return {"checkout": checkout}


async def _authenticate(
    request: Request, capability: str
) -> tuple[InternalRequestAuthenticator, Any]:
    auth: InternalRequestAuthenticator | None = getattr(request.app.state, "internal_auth", None)
    if auth is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="internal_auth_not_configured",
        )
    return auth, await auth.authenticate(request, capability)


def _require_local_ci(request: Request) -> None:
    settings = getattr(request.app.state, "settings", None)
    if settings is None or settings.environment not in {"local", "ci"}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="demo_checkout_unavailable"
        )


def _checkout_domain_error(error: DBAPIError, *, completing: bool = False) -> HTTPException:
    sqlstate = getattr(error.orig, "sqlstate", None)
    if completing and sqlstate == "28000":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid_demo_coupon",
        )
    if sqlstate in {"28000", "42501"}:
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="demo_checkout_unavailable",
        )
    if sqlstate == "23505":
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="idempotency_conflict")
    if sqlstate == "40001":
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="checkout_conflict")
    if sqlstate == "22023":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid_demo_checkout_request",
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="demo_checkout_unavailable",
    )
