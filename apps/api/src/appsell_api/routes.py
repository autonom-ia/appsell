from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from .internal_requests import AuthenticatedInternalRequest, InternalRequestAuthenticator

router = APIRouter(prefix="/v1")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class IdentitySyncBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    email: str
    email_verified: bool

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


class SellerOnboardingBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str
    slug: str
    display_name: str

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except ValueError as error:
            raise ValueError("invalid idempotency key") from error
        if str(parsed) != value:
            raise ValueError("invalid idempotency key")
        return value

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        if not 3 <= len(value) <= 63 or _SLUG.fullmatch(value) is None:
            raise ValueError("invalid seller slug")
        return value

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        if (
            value != value.strip()
            or not 2 <= len(value) <= 120
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        ):
            raise ValueError("invalid seller display name")
        return value


@router.put("/me")
async def sync_me(request: Request) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "identity.sync")
    await _require_no_seller(internal)
    body = await _model_from_body(IdentitySyncBody, internal)
    envelope = internal.envelope
    try:
        async with auth.domain_session(envelope) as session:
            identity_id = await session.scalar(
                text(
                    "select app_private.sync_identity("
                    ":identity_user_id,:issuer,:subject,:email,:verified)"
                ),
                {
                    "identity_user_id": envelope.identity.user_id,
                    "issuer": envelope.identity.issuer,
                    "subject": envelope.identity.sub,
                    "email": body.email,
                    "verified": body.email_verified,
                },
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    if identity_id != envelope.identity.user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return {"id": str(identity_id), "email": body.email, "email_verified": body.email_verified}


@router.get("/me")
async def read_me(request: Request) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "platform.grants.read")
    await _require_no_seller(internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            identity = (
                (
                    await session.execute(
                        text(
                            "select id, email_normalized, email_verified "
                            "from identity_user where id = :identity_user_id"
                        ),
                        {"identity_user_id": internal.envelope.identity.user_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            grants = (
                (
                    await session.execute(
                        text(
                            "select role from platform_grant "
                            "where identity_user_id = :identity_user_id and status = 'active' "
                            "order by role"
                        ),
                        {"identity_user_id": internal.envelope.identity.user_id},
                    )
                )
                .scalars()
                .all()
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    if identity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="identity_not_found")
    return {
        "id": str(identity["id"]),
        "email": identity["email_normalized"],
        "email_verified": identity["email_verified"],
        "platform_roles": list(grants),
    }


@router.get("/me/sellers")
async def list_sellers(request: Request) -> dict[str, Any]:
    auth, internal = await _authenticate(request, "seller.list")
    await _require_no_seller(internal)
    try:
        async with auth.domain_session(internal.envelope) as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "select organization.id, organization.slug, organization.display_name, "
                            "membership.role from seller_organization organization "
                            "join seller_membership membership "
                            "on membership.seller_organization_id = organization.id "
                            "where membership.identity_user_id = :identity_user_id "
                            "and membership.status = 'active' order by organization.created_at"
                        ),
                        {"identity_user_id": internal.envelope.identity.user_id},
                    )
                )
                .mappings()
                .all()
            )
    except DBAPIError as error:
        raise _domain_error(error) from error
    return {
        "sellers": [
            {
                "id": str(row["id"]),
                "slug": row["slug"],
                "display_name": row["display_name"],
                "role": row["role"],
            }
            for row in rows
        ]
    }


@router.post("/me/sellers", status_code=status.HTTP_201_CREATED)
async def onboard_seller(request: Request) -> dict[str, str]:
    auth, internal = await _authenticate(request, "seller.onboard")
    await _require_no_seller(internal)
    body = await _model_from_body(SellerOnboardingBody, internal)
    envelope = internal.envelope
    try:
        async with auth.domain_session(envelope) as session:
            seller_id = await session.scalar(
                text(
                    "select app_private.onboard_seller("
                    ":identity_user_id,:slug,:display_name,:idempotency_key,"
                    ":body_sha256,:request_id)"
                ),
                {
                    "identity_user_id": envelope.identity.user_id,
                    "slug": body.slug,
                    "display_name": body.display_name,
                    "idempotency_key": UUID(body.idempotency_key),
                    "body_sha256": envelope.body_sha256,
                    "request_id": envelope.request_id,
                },
            )
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="seller_conflict"
        ) from error
    except DBAPIError as error:
        raise _domain_error(error) from error
    return {"id": str(seller_id)}


async def _authenticate(
    request: Request, capability: str
) -> tuple[InternalRequestAuthenticator, AuthenticatedInternalRequest]:
    auth: InternalRequestAuthenticator | None = getattr(request.app.state, "internal_auth", None)
    if auth is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="internal_auth_not_configured",
        )
    return auth, await auth.authenticate(request, capability)


async def _require_no_seller(internal: AuthenticatedInternalRequest) -> None:
    if internal.envelope.seller_organization_id is not None:
        await internal.consume_rejected_request()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


async def _model_from_body(model: type[BaseModel], internal: AuthenticatedInternalRequest) -> Any:
    try:
        value = json.loads(internal.body, object_pairs_hook=_reject_duplicate_keys)
        return model.model_validate(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as error:
        await internal.consume_rejected_request()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid_request_body",
        ) from error


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _domain_error(error: DBAPIError) -> HTTPException:
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate in {"23505"}:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="resource_conflict")
    if sqlstate in {"22023"}:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid_domain_request",
        )
    if sqlstate in {"28000", "42501"}:
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="domain_unavailable",
    )
